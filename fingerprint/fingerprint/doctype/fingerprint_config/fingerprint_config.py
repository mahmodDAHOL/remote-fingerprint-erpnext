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
}

config = dotdict(fing_config)

device_punch_values_IN = getattr(fing_config, 'device_punch_values_IN', [1,4])
device_punch_values_OUT = getattr(fing_config, 'device_punch_values_OUT', [0,5])
ERPNEXT_VERSION = getattr(fing_config, 'ERPNEXT_VERSION', 14)

if not os.path.exists(config.LOGS_DIRECTORY):
    os.makedirs(config.LOGS_DIRECTORY)

error_logger = setup_logger('error_logger', '/'.join([config.LOGS_DIRECTORY, 'error.log']), logging.ERROR)
info_logger = setup_logger('info_logger', '/'.join([config.LOGS_DIRECTORY, 'logs.log']))
status = PickleDB('/'.join([config.LOGS_DIRECTORY, 'status.json']))

def main():
    try:
        for file_name in os.listdir('/home/frappe/frappe-bench/sites/frontend/private/files'):
            file_path = os.path.join('/home/frappe/frappe-bench/sites/frontend/private/files', file_name)
            info_logger.info("Processing File: "+ file_path)
            try:
                pull_process_and_push_data(file_path)
                info_logger.info("Successfully processed File: "+ file_path)
            except:
                error_logger.exception('exception when calling pull_process_and_push_data function for file '+ file_path)
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

def edit_attendance(record):
    record['timestamp'] = datetime.datetime.fromtimestamp(record['timestamp'])
    return record

def pull_process_and_push_data(file_path):
    
    """ Takes a single device config as param and pulls data from that device.

    params:
    device: a single device config object from the local_config file
    device_attendance_logs: fetching from device is skipped if this param is passed. used to restart failed fetches from previous runs.
    """

    content = open(file_path, 'r').read()
    attendances = json.loads(content)
    updated_attendances = [edit_attendance(att) for att in attendances]
    attendances = sorted(updated_attendances, key=lambda x: x['timestamp'])

    device_attendance_logs = attendances
    if not device_attendance_logs:
            return
    import_start_date = _safe_convert_date(config.IMPORT_START_DATE, "%Y%m%d")
    
    for i, x in enumerate(device_attendance_logs):
        if x['timestamp'] >= import_start_date:
            index_of_last = i
            break

    # Process each log in the determined range
    for device_attendance_log in device_attendance_logs[index_of_last:]:
        if device_attendance_log['punch'] in device_punch_values_OUT:
            punch_direction = 'OUT'
        elif device_attendance_log['punch'] in device_punch_values_IN:
            punch_direction = 'IN'
        else:
            punch_direction = None

        try:
            add_log_based_on_employee_field(
                device_attendance_log['user_id'], device_attendance_log['timestamp'], "device_id", punch_direction
            )

        except Exception as e:
            # error_logger.exception(f"Error fetching user {device_attendance_log['user_id']}: {e}")
            pass


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


class fingerprint_config(Document):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  
        # if self.get('device_ip_list'):
        #     device_IP_list = self.get('device_ip_list').split(",")
        #     device_IP_list = [item.strip() for item in device_IP_list]

        #     device_ID_list = self.get('device_id_list').split(",")
        #     device_ID_list = [item.strip() for item in device_ID_list]
        
        #     self.devices = []
        #     for device_ip, device_id in zip(device_IP_list, device_ID_list):
        #         self.devices.append({'device_id':device_id,'ip':device_ip, 'punch_direction': 'AUTO', 'clear_from_device_on_fetch': False})
            
        ERPNEXT_VERSION = 14
        IMPORT_START_DATE = self.get('import_start_date')
        if IMPORT_START_DATE:
            IMPORT_START_DATE = IMPORT_START_DATE.replace('-','')
            
        LOGS_DIRECTORY = 'logs' # logs of this script is stored in this directory


        fing_config = {
            'ERPNEXT_VERSION': ERPNEXT_VERSION,
            'IMPORT_START_DATE':IMPORT_START_DATE,
            'LOGS_DIRECTORY': LOGS_DIRECTORY,
        }

        global config
        config = dotdict(fing_config)

    def get(self, key):
        return self.__dict__.get(key, None)

    def before_save(self):	
        info_logger.info(str(config))
        
        main()
