#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from py_simple_ctrl.core.simple_ctrl import simple_ctrl_manager
from py_simple_ctrl.core.dev_button_led import simple_ctrl_button_led
from py_simple_ctrl.core.dev_smart_ir import simple_ctrl_smart_ir
from py_simple_ctrl.core.dev_sensor import simple_ctrl_sensor
from py_simple_ctrl.core.dev_voice_led import simple_ctrl_voice_led
import time
import threading
import queue

class dev_manager(threading.Thread):
    def __init__(self, passwd_file):
        super().__init__()
        self.dev_password = { }
        try:
            with open(passwd_file, 'r', encoding='utf-8') as f:
                _data = f.readlines()
                _data = [_.replace('\r', '').replace('\n', '') for _ in _data]
                self.dev_password = {_[:14]: _[14:] for _ in _data}
        except Exception as e:
            print(e)
        print('The password has been loaded')
        # print(self.dev_password)
        self.dev_center = { }
        self.dev_center_lock = threading.Lock()
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

    def dev_connect(self, dev_id):
        try:
            if dev_id not in self.dev_password:
                return
            dev_passwd = self.dev_password[dev_id]
            dev = self.server.device_factory(
                dev_id, dev_passwd,
                lambda x,y : self.dev_on_change(dev_id, x, y)
            )
            dev.connect(3)
            runtime_data = { }
            runtime_data['dev'] = dev
            info_name = dev.info_get_name()
            info_type = dev.info_get_type()
            print(f'# name:{info_name}, type:{info_type}')
            runtime_data['name'] = info_name
            runtime_data['type'] = info_type
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
        except Exception as e:
            print(e)

    def run(self):
        '''
        Manage Devices
        '''
        print('Manager thread started')
        while self.running:
            try:
                # Device online and offline
                m_event = self.manager_event.get(timeout=1)
                if m_event is None:
                    break
                event, dev_id = m_event
                if event == 'online':
                    self.dev_connect(dev_id)
                elif event == 'offline':
                    self.dev_disconnect(dev_id)
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
        self.manager_event.put((event, dev_id))

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

def main():
    main_loop = True
    while main_loop:
        manager = None
        try:
            manager = dev_manager('devinfo.txt')
            manager.manager_start()
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            print('Program interrupted by user')
            main_loop = False
        except Exception as e:
            print(e)
        finally:
            manager.manager_stop()
    print('Main exited')

if __name__ == '__main__':
    main()
