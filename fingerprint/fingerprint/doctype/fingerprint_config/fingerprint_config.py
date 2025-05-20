# Copyright (c) 2025, fingerprint and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

import requests
import datetime
import json
import os
import logging
from pickledb import PickleDB
from zk import ZK, const
from logging.handlers import RotatingFileHandler
from hrms.hr.doctype.employee_checkin.employee_checkin import add_log_based_on_employee_field



class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def setup_logger(name, log_file, level=logging.INFO, formatter=None):
    
    if not formatter:
        formatter = logging.Formatter('%(asctime)s\t%(levelname)s\t%(message)s')

    handler = RotatingFileHandler(log_file, maxBytes=10000000, backupCount=50)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.hasHandlers():
        logger.addHandler(handler)

    return logger


EMPLOYEE_NOT_FOUND_ERROR_MESSAGE = "No Employee found for the given employee field value"
EMPLOYEE_INACTIVE_ERROR_MESSAGE = "Transactions cannot be created for an Inactive Employee"
DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE = "This employee already has a log with the same timestamp"
allowlisted_errors = [EMPLOYEE_NOT_FOUND_ERROR_MESSAGE, EMPLOYEE_INACTIVE_ERROR_MESSAGE, DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE]


fing_config = {
    'ERPNEXT_VERSION': 14,
    'IMPORT_START_DATE':None,
    'LOGS_DIRECTORY': 'logs',
    'devices': None
}

config = dotdict(fing_config)

device_punch_values_IN = getattr(fing_config, 'device_punch_values_IN', [0,4])
device_punch_values_OUT = getattr(fing_config, 'device_punch_values_OUT', [1,5])
ERPNEXT_VERSION = getattr(fing_config, 'ERPNEXT_VERSION', 14)

if not os.path.exists(config.LOGS_DIRECTORY):
    os.makedirs(config.LOGS_DIRECTORY)

error_logger = setup_logger('error_logger', '/'.join([config.LOGS_DIRECTORY, 'error.log']), logging.ERROR)
info_logger = setup_logger('info_logger', '/'.join([config.LOGS_DIRECTORY, 'logs.log']))
status = PickleDB('/'.join([config.LOGS_DIRECTORY, 'status.json']))

def main():
    try:
        for device_num, device in enumerate(config.devices):
            # device = config.devices[0]
            device_attendance_logs = None
            info_logger.info("Processing Device: "+ device['device_id'])
            try:
                frappe.publish_progress(device_num/len(config.devices)*100, title=f"Fetching data from fingerprint device {device_num+1}...", description = "please wait until fetching data" )
                pull_process_and_push_data(device, device_attendance_logs)
                frappe.publish_progress((device_num+1)/len(config.devices)*100, title=f"Fetching data from fingerprint {device_num+1} device is finished", description = "done" )

                info_logger.info("Successfully processed Device: "+ device['device_id'])
            except:
                error_logger.exception('exception when calling pull_process_and_push_data function for device'+json.dumps(device, default=str))
            info_logger.info("Mission Accomplished!")
    except:
        error_logger.exception('exception has occurred in the main function...')

def sort_file_by_timestamp(file_path):
    # Read all lines from the file
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Parse each line, extract the timestamp, and store it with the line
    parsed_lines = []
    for line in lines:
        try:
            # Extract the JSON part of the line (last column)
            json_part = line.strip().split("\t")[-1]
            data = json.loads(json_part)

            # Parse the timestamp into a datetime object
            timestamp = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")
            parsed_lines.append((timestamp, line))
        except Exception as e:
            print(f"Error parsing line: {line}. Error: {e}")

    # Sort the lines by the timestamp
    parsed_lines.sort(key=lambda x: x[0])

    # Write the sorted lines back to the file
    with open(file_path, 'w') as file:
        for _, line in parsed_lines:
            file.write(line)

def pull_process_and_push_data(device, device_attendance_logs=None):
    
    """ Takes a single device config as param and pulls data from that device.

    params:
    device: a single device config object from the local_config file
    device_attendance_logs: fetching from device is skipped if this param is passed. used to restart failed fetches from previous runs.
    """
    attendance_success_log_file = '_'.join(["attendance_success_log", device['device_id']])
    attendance_failed_log_file = '_'.join(["attendance_failed_log", device['device_id']])
    attendance_success_logger = setup_logger(attendance_success_log_file, '/'.join(['logs', attendance_success_log_file])+'.log')
    attendance_failed_logger = setup_logger(attendance_failed_log_file, '/'.join(['logs', attendance_failed_log_file])+'.log')
    if not device_attendance_logs:
        device_attendance_logs = get_all_attendance_from_device(device['ip'], device_id=device['device_id'], clear_from_device_on_fetch=device['clear_from_device_on_fetch'])
        if not device_attendance_logs:
            return
    import_start_date = _safe_convert_date(config.IMPORT_START_DATE, "%Y%m%d")
    for i, x in enumerate(device_attendance_logs):
            if x['timestamp'] >= import_start_date:
                index_of_last = i
                break
        # Process each log in the determined range
    for device_attendance_log in device_attendance_logs[index_of_last+1:]:
        punch_direction = device['punch_direction']
        if punch_direction == 'AUTO':
            if device_attendance_log['punch'] in device_punch_values_OUT:
                punch_direction = 'OUT'
            elif device_attendance_log['punch'] in device_punch_values_IN:
                punch_direction = 'IN'
            else:
                punch_direction = None

        try:
            add_log_based_on_employee_field(
                device_attendance_log['user_id'], device_attendance_log['timestamp'], device['device_id'], punch_direction
            )

        except Exception as e:
            # error_logger.exception(f"Error fetching user {device_attendance_log['user_id']}: {e}")
            pass



def get_all_attendance_from_device(ip, port=4370, timeout=30, device_id=None, clear_from_device_on_fetch=False):
    
    zk = ZK(ip, port=port, timeout=timeout)
    conn = None
    attendances = []
    try:
        conn = zk.connect()
        x = conn.disable_device()
        # device is disabled when fetching data
        info_logger.info("\t".join((ip, "Device Disable Attempted. Result:", str(x))))
        attendances = conn.get_attendance()
        info_logger.info("\t".join((ip, "Attendances Fetched:", str(len(attendances)))))
        status.set(f'{device_id}_push_timestamp', None)
        status.set(f'{device_id}_pull_timestamp', str(datetime.datetime.now()))
        status.save()
        if len(attendances):
            # keeping a backup before clearing data incase the programs fails.
            # if everything goes well then this file is removed automatically at the end.
            dump_file_name = get_dump_file_name_and_directory(device_id, ip)

            with open(dump_file_name, 'w+') as f:
                f.write(json.dumps(list(map(lambda x: x.__dict__, attendances)), default=datetime.datetime.timestamp))
        x = conn.enable_device()
        info_logger.info("\t".join((ip, "Device Enable Attempted. Result:", str(x))))
    except:
        error_logger.exception(str(ip)+' exception when fetching from device...')
        raise Exception('Device fetch failed.')
    finally:
        if conn:
            conn.disconnect()
    return list(map(lambda x: x.__dict__, attendances))



def update_shift_last_sync_timestamp(shift_type_device_mapping):
    
    """
    ### algo for updating the sync_current_timestamp
    - get a list of devices to check
    - check if all the devices have a non 'None' push_timestamp
        - check if the earliest of the pull timestamp is greater than sync_current_timestamp for each shift name
            - then update this min of pull timestamp to the shift

    """
    for shift_type_device_map in shift_type_device_mapping:
        all_devices_pushed = True
        pull_timestamp_array = []
        for device_id in shift_type_device_map['related_device_id']:
            if not status.get(f'{device_id}_push_timestamp'):
                all_devices_pushed = False
                break
            pull_timestamp_array.append(_safe_convert_date(status.get(f'{device_id}_pull_timestamp'), "%Y-%m-%d %H:%M:%S.%f"))
        if all_devices_pushed:
            min_pull_timestamp = min(pull_timestamp_array)
            if isinstance(shift_type_device_map['shift_type_name'], str): # for backward compatibility of config file
                shift_type_device_map['shift_type_name'] = [shift_type_device_map['shift_type_name']]
            for shift in shift_type_device_map['shift_type_name']:
                try:
                    sync_current_timestamp = _safe_convert_date(status.get(f'{shift}_sync_timestamp'), "%Y-%m-%d %H:%M:%S.%f")
                    if (sync_current_timestamp and min_pull_timestamp > sync_current_timestamp) or (min_pull_timestamp and not sync_current_timestamp):
                        response_code = send_shift_sync_to_erpnext(shift, min_pull_timestamp)
                        if response_code == 200:
                            status.set(f'{shift}_sync_timestamp', str(min_pull_timestamp))
                            status.save()
                except:
                    error_logger.exception('Exception in update_shift_last_sync_timestamp, for shift:'+shift)

def send_shift_sync_to_erpnext(shift_type_name, sync_timestamp):
    
    url = config.ERPNEXT_URL + "/api/resource/Shift Type/" + shift_type_name
    headers = {
        'Authorization': "token "+ config.ERPNEXT_API_KEY + ":" + config.ERPNEXT_API_SECRET,
        'Accept': 'application/json'
    }
    data = {
        "last_sync_of_checkin" : str(sync_timestamp)
    }
    try:
        response = requests.request("PUT", url, headers=headers, data=json.dumps(data))
        if response.status_code == 200:
            info_logger.info("\t".join(['Shift Type last_sync_of_checkin Updated', str(shift_type_name), str(sync_timestamp.timestamp())]))
        else:
            error_str = _safe_get_error_str(response)
            error_logger.error('\t'.join(['Error during ERPNext Shift Type API Call.', str(shift_type_name), str(sync_timestamp.timestamp()), error_str]))
        return response.status_code
    except:
        error_logger.exception("\t".join(['exception when updating last_sync_of_checkin in Shift Type', str(shift_type_name), str(sync_timestamp.timestamp())]))

def get_last_line_from_file(file):
    
    line = None
    if os.stat(file).st_size < 5000:
        # quick hack to handle files with one line
        with open(file, 'r') as f:
            for line in f:
                pass
    else:
        # optimized for large log files
        with open(file, 'rb') as f:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
            line = f.readline().decode()
    return line

def get_first_line_from_file(file):
    with open(file, 'r') as f:
        # Read the first line
        line = f.readline().strip()
        return line if line else None 


def get_dump_file_name_and_directory(device_id, device_ip):
    
    return 'logs' + '/' + device_id + "_" + device_ip.replace('.', '_') + '_last_fetch_dump.json'

def _apply_function_to_key(obj, key, fn):
    
    obj[key] = fn(obj[key])
    return obj

def _safe_convert_date(datestring, pattern):
    
    try:
        return datetime.datetime.strptime(datestring, pattern)
    except:
        return None

def _safe_get_error_str(res):
    
    try:
        error_json = json.loads(res._content)
        if 'exc' in error_json: # this means traceback is available
            error_str = json.loads(error_json['exc'])[0]
        else:
            error_str = json.dumps(error_json)
    except:
        error_str = str(res.__dict__)
    return error_str

def generate_keys(user):

    user_details = frappe.get_doc('User', user)
    api_secret = frappe.generate_hash(length=15)

    if not user_details.api_key:
        api_key = frappe.generate_hash(length=15)
        user_details.api_key = api_key

    frappe.db.set_value('User', user, 'api_secret', api_secret)

    return api_secret



class fingerprint_config(Document):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  
        if self.get('device_ip_list'):
            device_IP_list = self.get('device_ip_list').split(",")
            device_IP_list = [item.strip() for item in device_IP_list]

            device_ID_list = self.get('device_id_list').split(",")
            device_ID_list = [item.strip() for item in device_ID_list]
        
            self.devices = []
            for device_ip, device_id in zip(device_IP_list, device_ID_list):
                self.devices.append({'device_id':device_id,'ip':device_ip, 'punch_direction': 'AUTO', 'clear_from_device_on_fetch': False})
            
            ERPNEXT_VERSION = 14
            IMPORT_START_DATE = self.get('import_start_date')
            LOGS_DIRECTORY = 'logs' # logs of this script is stored in this directory


            fing_config = {
                'ERPNEXT_VERSION': ERPNEXT_VERSION,
                'IMPORT_START_DATE':IMPORT_START_DATE,
                'LOGS_DIRECTORY': LOGS_DIRECTORY,
                'devices': self.devices
            }

            global config
            config = dotdict(fing_config)

    def get(self, key):
        return self.__dict__.get(key, None)

    def before_save(self):	
        main()
