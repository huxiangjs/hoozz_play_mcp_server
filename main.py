#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from py_simple_ctrl.core.simple_ctrl import simple_ctrl_manager
from py_simple_ctrl.core.dev_button_led import simple_ctrl_button_led
from py_simple_ctrl.core.dev_smart_ir import simple_ctrl_smart_ir
from py_simple_ctrl.core.dev_sensor import simple_ctrl_sensor
from py_simple_ctrl.core.dev_voice_led import simple_ctrl_voice_led
import os
import threading
import queue
import argparse
from mcp.server.fastmcp import FastMCP
import asyncio
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

server_name = 'Hoozz Play MCP Server'

parser = argparse.ArgumentParser(description=server_name)
parser.add_argument('--path', type=str, required=False, help='Password file path')
args = parser.parse_args()

passwd_path = args.path if args.path else 'devinfo.txt'

class dev_password(FileSystemEventHandler):
    '''Load and monitor password file'''

    def __init__(self, passwd_file, on_change):
        super().__init__()
        self._on_change = on_change
        self._file_path = passwd_file
        self._path = os.path.dirname(self._file_path)
        self._path = '.' if len(self._path) == 0 else self._path
        self._name = os.path.basename(self._file_path)
        self._data_lock = threading.Lock()
        self._observer = None
        self.parse_file()

    def parse_file(self):
        self._password = { }
        with open(self._file_path, 'r', encoding='utf-8') as f:
            _data = f.readlines()
            _data = [_.replace('\r', '').replace('\n', '') for _ in _data]
            self._password = {_[:14]: _[14:] for _ in _data}
        print('The password has been loaded')
        # print(self._password)

    def on_modified(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith(self._name):
            with self._data_lock:
                self.parse_file()
            self._on_change()

    def start(self):
        self._observer = Observer()
        self._observer.schedule(self, self._path, recursive=False)
        self._observer.start()

    def stop(self):
        self._observer.stop()
        self._observer.join()

    @property
    def data(self):
        with self._data_lock:
            password = self._password.copy()
        return password

class dev_manager(threading.Thread):
    def __init__(self, dev_password):
        super().__init__()
        self.dev_center = { }
        self.dev_center_lock = threading.Lock()
        self.password = dev_password
        self.running = False
        self.server = None
        self.manager_event = queue.Queue(maxsize=0)

    def dev_on_change(self, dev_id, event, data):
        if event == 'state':
            print(dev_id, event, data)
        with self.dev_center_lock:
            if dev_id not in self.dev_center:
                return
            runtime_data = self.dev_center[dev_id]
            runtime_data[event] = data
            # print(event, data)
            # The keys have changed; the device needs to be refreshed.
            if isinstance(runtime_data['dev'], simple_ctrl_smart_ir) and event == 'key':
                print('The IR keys have changed')
                runtime_data['state'] = 'dead'

    def dev_connect(self, dev_id):
        try:
            password = self.password.data
            if dev_id not in password:
                return False
            dev_passwd = password[dev_id]
            dev = self.server.device_factory(
                dev_id, dev_passwd,
                lambda x,y : self.dev_on_change(dev_id, x, y)
            )
            dev.connect(3)
            runtime_data = { }
            runtime_data['dev'] = dev
            info_name = dev.info_get_name()
            # info_type = dev.info_get_type()
            info_type = type(dev).__name__
            print(f'# name:{info_name}, type:{info_type}')
            runtime_data['name'] = info_name
            # runtime_data['type'] = info_type
            if isinstance(dev, simple_ctrl_button_led):
                rgb = dev.get_color()
                print('    color:', rgb)
                runtime_data['color'] = rgb
            elif isinstance(dev, simple_ctrl_voice_led):
                rgb = dev.get_color()
                print('    color:', rgb)
                runtime_data['color'] = rgb
            elif isinstance(dev, simple_ctrl_smart_ir):
                key_count = dev.get_count()
                print('    key_count:', key_count)
                key_list = [ ]
                for i in range(key_count):
                    key = dev.get_item(i)
                    print(f'    key_name: [{i}] {key}')
                    key_list.append(key)
                runtime_data['key_list'] = key_list
            elif isinstance(dev, simple_ctrl_sensor):
                sensor_count = dev.get_count()
                print('    sensor_count:', sensor_count)
                sensor_info = { }
                for i in range(sensor_count):
                    type_str, sensor_id, sensor_name = dev.get_item(i)
                    if type_str not in sensor_info:
                        sensor_info[type_str] = { }
                    sensor_info[type_str][sensor_id] = sensor_name
                    print(f'    [{i}] {type_str}: [{sensor_id}]{sensor_name}')
                runtime_data['sensor_info'] = sensor_info
            with self.dev_center_lock:
                self.dev_center[dev_id] = runtime_data
            return True
        except Exception as e:
            print(f'Connection failed: {e}')
            return False

    def run(self):
        '''
        Manage Devices
        '''
        print('Manager thread started')
        deferred_dict = { }
        while self.running:
            try:
                # Device online and offline
                m_event = self.manager_event.get(timeout=1)
                if m_event is None:
                    break
                event, dev_name, dev_id = m_event
                if event == 'online':
                    ok = self.dev_connect(dev_id)
                    if not ok:
                        print(f'Connection to {dev_id} failed; added to the deferred_set')
                        deferred_dict[dev_id] = 5
                elif event == 'offline':
                    if dev_id in deferred_dict:
                        del deferred_dict[dev_id]
                    self.dev_disconnect(dev_id)
                elif event == 'name_change':
                    with self.dev_center_lock:
                        if dev_id in self.dev_center:
                            runtime_data = self.dev_center[dev_id]
                            runtime_data['name'] = dev_name
                elif event == 'passwd_change':
                    for item in deferred_dict.copy().keys():
                        ok = self.dev_connect(item)
                        if ok:
                            del deferred_dict[item]
                        deferred_dict[item] = 5 # reset count
                self.manager_event.task_done()
            except queue.Empty:
                # Re-connecting disconnected devices
                # print(self.dev_center)
                retry_dict = { }
                with self.dev_center_lock:
                    for k, v in self.dev_center.items():
                        if 'state' not in v:
                            continue
                        if v['state'] == 'ready':
                            continue
                        retry_dict[k] = v
                for k, v in retry_dict.items():
                    v['dev'].disconnect()
                    print(f"Re-connecting: {v['name']}")
                    self.dev_connect(k)
                # Try to connect to all devices on the deferred_dict
                for id, count in deferred_dict.copy().items():
                    if count == 0:
                        continue
                    ok = self.dev_connect(id)
                    if ok:
                        del deferred_dict[id]
                    deferred_dict[id] -= 1
        runtime_list = []
        with self.dev_center_lock:
            runtime_list = list(self.dev_center.values())
            self.dev_center.clear()
        for runtime_data in runtime_list:
            runtime_data['dev'].disconnect()
        print('Manager thread stopped')

    def dev_disconnect(self, dev_id):
        runtime_data = None
        with self.dev_center_lock:
            if dev_id not in self.dev_center:
                return
            runtime_data = self.dev_center[dev_id]
            del self.dev_center[dev_id]
        if runtime_data:
            runtime_data['dev'].disconnect()

    def manager_on_change(self, event, dev_info):
        dev_name = dev_info.name
        dev_id = dev_info.id
        print(f'[{event}] {dev_name} ({dev_id})')
        self.manager_event.put((event, dev_name, dev_id))

    def manager_on_passwd_change(self):
        self.manager_event.put(('passwd_change', None, None))

    def manager_start(self):
        class_list = [
            simple_ctrl_button_led,
            simple_ctrl_smart_ir,
            simple_ctrl_sensor,
            simple_ctrl_voice_led
        ]
        self.running = True
        self.server = simple_ctrl_manager(class_list, self.manager_on_change)
        self.server.start()
        self.start()

    def manager_stop(self):
        self.running = False
        self.manager_event.put(None)
        self.server.stop()
        self.join()

def run_mcp_server(manager):
    '''
    Enable the MCP service.
    This function never returns unless an exception occurs.
    '''

    mcp = FastMCP(
        name=server_name,
        # host='localhost',
        host='0.0.0.0',
        port=8000,
        log_level='INFO',
        streamable_http_path='/mcp',
        # auth=xxx,
    )

    @mcp.tool()
    def manager_list_available_dev() -> list[dict]:
        '''List all available devices.

        Returns: [{device_id, device_name, class_name, class_desc}]
        '''

        result_data = [ ]
        with manager.dev_center_lock:
            for k, v in manager.dev_center.items():
                dev = v['dev']
                dev_id = k
                dev_name = v['name']
                class_name = type(dev).__name__.strip()
                class_desc = type(dev).__doc__.strip()
                result_data.append({
                    'device_id' : dev_id,
                    'device_name' : dev_name,
                    'class_name' : class_name,
                    'class_desc' : class_desc,
                })
        return result_data

    @mcp.tool()
    def dev_button_led_get_color(device_id: str) -> dict:
        '''Get LED color from class_name 'simple_ctrl_button_led'.

        Args: device_id
        Returns: {msg, red(0-255), green(0-255), blue(0-255)}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_button_led):
                    raise Exception('Mismatched device `class_name`')
                r, g, b = runtime_data['color']
                result_data = {
                    'msg': f'success',
                    'red' : r,
                    'green' : g,
                    'blue' : b,
                }
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_button_led_set_color(device_id: str, red: int, green: int, blue: int) -> dict:
        '''Set LED color on class_name 'simple_ctrl_button_led'.

        Args: device_id, red(0-255), green(0-255), blue(0-255)
        Returns: {msg}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_button_led):
                    raise Exception('Mismatched device `class_name`')
            dev.set_color((red, green, blue))
            result_data = {'msg': f'success'}
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_voice_led_get_color(device_id: str) -> dict:
        '''Get LED color from class_name 'simple_ctrl_voice_led'.

        Args: device_id
        Returns: {msg, red(0-255), green(0-255), blue(0-255)}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_voice_led):
                    raise Exception('Mismatched device `class_name`')
                r, g, b = runtime_data['color']
                result_data = {
                    'msg': f'success',
                    'red' : r,
                    'green' : g,
                    'blue' : b,
                }
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_voice_led_set_color(device_id: str, red: int, green: int, blue: int) -> dict:
        '''Set LED color on class_name 'simple_ctrl_voice_led'.

        Args: device_id, red(0-255), green(0-255), blue(0-255)
        Returns: {msg}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_voice_led):
                    raise Exception('Mismatched device `class_name`')
            dev.set_color((red, green, blue))
            result_data = {'msg': f'success'}
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_smart_ir_get_key_list(device_id: str) -> dict:
        '''Get IR key list from class_name 'simple_ctrl_smart_ir'.

        Args: device_id
        Returns: {msg, key_list: [key_name, key_name, ...]}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_smart_ir):
                    raise Exception('Mismatched device `class_name`')
                key_list = runtime_data['key_list']
                result_data = {
                    'msg': f'success',
                    'key_list' : key_list,
                }
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_smart_ir_press_key(device_id: str, key_name: str) -> dict:
        '''Press IR key on class_name 'simple_ctrl_smart_ir'.

        Args: device_id, key_name
        Returns: {msg}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_smart_ir):
                    raise Exception('Mismatched device `class_name`')
            dev.tx_send(key_name)
            result_data = {'msg': f'success'}
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_sensor_get_sensor_info(device_id: str) -> dict:
        '''Get all sensor info from class_name 'simple_ctrl_sensor' (device may have multiple).

        Args: device_id
        Returns: {msg, sensor_info: [{sensor_id, sensor_type, sensor_name}]}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_sensor):
                    raise Exception('Mismatched device `class_name`')
                sensor_info = runtime_data['sensor_info']
                merge_info = [ ]
                for type, info in sensor_info.items():
                    for id, name in info.items():
                        merge_info.append({
                            'sensor_id': id,
                            'sensor_type': type,
                            'sensor_name': name,
                        })
                result_data = {
                    'msg': f'success',
                    'sensor_info' : merge_info,
                }
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    @mcp.tool()
    def dev_sensor_get_sensor_data(device_id: str, query_list: list) -> dict:
        '''Query sensor data from class_name 'simple_ctrl_sensor'.

        Args: device_id, query_list([{sensor_id, sensor_type}])
        Returns: {msg, data: [{sensor_id, sensor_type, sensor_data}]}
        '''

        try:
            with manager.dev_center_lock:
                runtime_data = manager.dev_center[device_id]
                dev = runtime_data['dev']
                if not isinstance(dev, simple_ctrl_sensor):
                    raise Exception('Mismatched device `class_name`')
                data = [ ]
                for item in query_list:
                    if type(item) is dict:
                        if 'sensor_id' in item:
                            sensor_id = item['sensor_id']
                            sensor_type = item['sensor_type']
                            value, unit = runtime_data[sensor_type][sensor_id]
                            sensor_data = f'{value}{unit}'
                            data.append({
                                'sensor_id': sensor_id,
                                'sensor_type': sensor_type,
                                'sensor_data': sensor_data,
                            })
                        else:
                            sensor_type = item['sensor_type']
                            all_data = runtime_data[sensor_type]
                            for sensor_id, (value, unit) in all_data.items():
                                sensor_data = f'{value}{unit}'
                                data.append({
                                    'sensor_id': sensor_id,
                                    'sensor_type': sensor_type,
                                    'sensor_data': sensor_data,
                                })
                    elif type(item) is str:
                        sensor_type = item
                        all_data = runtime_data[sensor_type]
                        for sensor_id, (value, unit) in all_data.items():
                            sensor_data = f'{value}{unit}'
                            data.append({
                                'sensor_id': sensor_id,
                                'sensor_type': sensor_type,
                                'sensor_data': sensor_data,
                            })
                    else:
                        raise Exception('Invalid parameter format')
                result_data = {
                    'msg': f'success',
                    'data' : data,
                }
        except Exception as e:
            result_data = {'msg': f'error: {e}'}

        return result_data

    # Blocks on call
    mcp.run(transport='streamable-http')

def main():
    '''Main'''
    main_loop = True
    while main_loop:
        manager = None
        password = None
        try:
            def on_change():
                manager.manager_on_passwd_change()
            password = dev_password(passwd_path, on_change)
            password.start()
            manager = dev_manager(password)
            manager.manager_start()
            # Blocks on call
            run_mcp_server(manager)
        except FileNotFoundError as e:
            print(e)
            main_loop = False
        except KeyboardInterrupt:
            print('Program interrupted by user')
            main_loop = False
        except asyncio.exceptions.CancelledError as e:
            print(e)
            main_loop = False
        except Exception as e:
            print(e)
        finally:
            if manager:
                manager.manager_stop()
            if password:
                password.stop()
    print('Main exited')

if __name__ == '__main__':
    main()
