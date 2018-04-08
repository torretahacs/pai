import paradox_messages as msg
from serial_connection import *
import logging
import sys
import time
import json

from config_defaults import *
from config import *

MEM_STATUS_BASE1 = 0x8000
MEM_STATUS_BASE2 = 0x1fe0
MEM_ZONE_START = 0x010
MEM_ZONE_END = 0x01f0
MEM_OUTPUT_START = 0x210
MEM_OUTPUT_END = 0x2f0
MEM_PARTITION_START = 0x310
MEM_PARTITION_END = 0x310

logger = logging.getLogger('PAI').getChild(__name__)

class Paradox:
    def __init__(self, connection, interface, retries=3, alarmeventmap="ParadoxMG5050", logger=logging.getLogger()):

        self.connection = connection
        self.retries = retries
        self.alarmeventmap = alarmeventmap
        self.interface = interface
        self.logger = logger

        # Keep track of alarm state
        self.labels = {'zone': {}, 'partition': {}, 'output': {}}
        self.zones = []
        self.partitions = []
        self.outputs = []
        self.power = dict()

    def connect(self): 
        try:
            reply = self.send_wait_for_reply(msg.InitiateCommunication, None)
            reply = self.send_wait_for_reply(msg.SerialInitialization, None)
            reply = self.send_wait_for_reply(message=reply.fields.data + reply.checksum)

            self.update_labels()
            return True
        except:
            self.logger.exception("Unable to connect to alarm")
            return False

    def loop(self):
        args = {}

        while True:
            i = 0
            while i < 3:
                args = dict(address=MEM_STATUS_BASE1 + i)
                reply = self.send_wait_for_reply(msg.Upload, args)
                self.handle_status(reply)
                i += 1
            
            # Listen for events    
            self.send_wait_for_reply(None)


    def send_wait_for_reply(self, message_type=None, args=None, message=None):

        if message is None and message_type is not None:
            message = message_type.build(dict(fields=dict(value=args)))
       
        retries = self.retries
        while retries >= 0:
            retries -= 1

            if message is not None:
                if logger.isEnabledFor(logging.DEBUG):
                    print(" -> ", end='')
                    for c in message:
                        print("{0:02x} ".format(c), end='')
                    print()
                self.connection.write(message)

            data = self.connection.read()

            # Retry if no data was available
            if data is None or len(data) == 0:
                time.sleep(0.25)
                continue
            
            if  logger.isEnabledFor(logging.DEBUG):
                print(" <- ", end='')
                for c in data:
                    print("{0:02x} ".format(c), end='')
                print()

            try:
                recv_message = msg.parse(data)
            except:
                logging.exception("Error parsing message")
                time.sleep(0.25)

            # Events are async
            if recv_message.fields.value.po.command == 0xe:
                self.handle_event(recv_message)
                time.sleep(0.25)
                continue

            return recv_message
           
        return None

    def update_labels(self):
        zone_template = dict(open=False, bypass=False, alarm=False, fire_alarm=False, shutdown=False, tamper=False, low_battery=False, supervision_trouble=False, timestamp=0)
        output_template = dict(on=False, pulse=False, tamper=False, supervision_trouble=False, timestamp=0)
        partition_template =dict(alarm=False, arm=False, arm_full=False, arm_sleep=False, arm_stay=False, timestamp=0)

        self.load_labels(self.zones, self.labels['zone'], MEM_ZONE_START, MEM_ZONE_END, template=zone_template)
        self.load_labels(self.outputs, self.labels['output'], MEM_OUTPUT_START, MEM_OUTPUT_END, template=output_template)
        self.load_labels(self.partitions, self.labels['partition'], MEM_PARTITION_START, MEM_PARTITION_END, template=partition_template)

        for partition in self.partitions[1:PARTITIONS + 1]:
            for k,v in partition.items():
                self.interface.change('partition', partition['label'], k, v)

        for zone in self.zones[1:ZONES + 1]:
            for k,v in zone.items():
                self.interface.change('zone', zone['label'], k, v)

        for output in self.outputs[1: OUTPUTS + 1]:
            for k,v in output.items():
                self.interface.change('output', output['label'], k, v)


    def load_labels(self, labelList, labelDict, start, end, step=0x10, template=dict(label='')):
        """Load labels from panel"""
        i = 1
        labelList.append("all")
        address = start
        while address <= end:      
            args = dict(address=address)
            reply = self.send_wait_for_reply(msg.Upload, args)
            
            payload = reply.fields.value.data

            for j in [0, 16]:
                label = payload[j:j+16].strip().decode('latin')

                if label not in labelDict.keys():
                    properties = template.copy()
                    properties['label'] = label
                    if len(labelList) <= i:
                        labelList.append(properties)
                    else:
                        labelList[i] = properties

                    labelDict[label] = i
                    i += 1 
            address += step



    def control_zone(self, zone, command):
        if command not in ['bypass', 'clear_bypass']:
            return False

        zones = []
        # if all or 0, select all
        if zone == 'all' or zone == '0':
            zones = list(range(1, len(self.zones)))
        else:
            # if set by name, look for it
            if zone in self.labels['zone']:
                zones = [self.labels['zone'][zone]]
            # if set by number, look for it
            elif zone.isdigit():
                number = int(zone)
                if number > 0 and number < len(self.zones):
                    zones = [number]
    
        # Not Found
        if len(zones) == 0:
            return False

        for e in zones:
            args = dict(zone=e,state=command)
            reply = self.send_wait_for_reply(msg.ZoneStateCommand, args)


    def control_partition(self, partition, command):
        if command not in ['arm', 'disarm', 'arm_stay', 'arm_sleep']:
            return False

        partitions = []
        # if all or 0, select all
        if partition == 'all' or partition == '0':
            partitions = list(range(1, len(self.partitions)))
        else:
            # if set by name, look for it
            if partition in self.labels['partition']:
                partitions = [self.labels['partition'][partition]]
            # if set by number, look for it
            elif partition.isdigit():
                number = int(partition)
                if number > 0 and number < len(self.partitions):
                    partitions = [number]
    
        # Not Found
        if len(partitions) == 0:
            return False

        for e in partitions:
            args = dict(partition=e,state=command)
            reply = self.send_wait_for_reply(msg.PartitionStateCommand, args)

        return True

    def control_output(self, output, command):
        if command not in ['on', 'off', 'pulse']:
            return False

        outputs = []
        # if all or 0, select all
        if output == 'all' or output == '0':
            outputs = list(range(1, len(self.outputs)))
        else:
            # if set by name, look for it
            if output in self.labels['output']:
                outputs = [self.labels['output'][output]]
            # if set by number, look for it
            elif output.isdigit():
                number = int(output)
                if number > 0 and number < len(self.outputs):
                    outputs = [number]
    
        # Not Found
        if len(outputs) == 0:
            return False

        for e in outputs:
            if command=='pulse':
                args = dict(output=e,state='on')
                reply = self.send_wait_for_reply(msg.OutputStateCommand, args)
                time.sleep(1)
                args = dict(output=e,state='off')
                reply = self.send_wait_for_reply(msg.OutputStateCommand, args)
            else:
                args = dict(output=e,state=command)
                reply = self.send_wait_for_reply(msg.OutputStateCommand, args)

        return True


    def handle_event(self, message):
        """Process Live Event Message and dispatch it to the interface module"""
        logger.debug("Handle Event: {}".format(message))
        event = message.fields.value.event

        major = event['major'][1]
        minor = ": {}".format(event['minor'][1])

        if event['type'] == 'Zone':
            label = self.zones[event['minor'][0]]['label']
            minor = ""
        elif event['type'] == 'Partition':
            label = self.particions[event['minor'][0]]['label']
            minor = ""
        elif event['type'] == 'Output':
            label = self.outputs[event['minor'][0]]['label']
            minor = ""
        else:
            label = event['type']

        if self.interface is not None:
            self.interface.event(element=event['type'], 
                                message=major+minor,
                                label=label, raw=event)

    def handle_status(self, message):
        """Handle MessageStatus"""
        if message.fields.value.address == 0:
            self.power.update(dict(vdc=message.fields.value.vdc, battery=message.fields.value.battery, dc=message.fields.value.dc))

            i = 1
            while i <= ZONES and i in message.fields.value.zone_status:
                v = message.fields.value.zone_status[i]
                for k1,v1 in v.items():
                    if k1 not in self.zones[i] or self.zones[i][k1] != v[k1]:
                        self.interface.change('zone', self.zones[i]['label'], k1, v[k1])

                self.zones[i].update(v)
                i+=1

        elif message.fields.value.address == 1:
            i = 1
            while i <= PARTITIONS and i in message.fields.value.partition_status:
                v = message.fields.value.partition_status[i]
                for k1,v1 in v.items():
                    if k1 not in self.partitions[i] or self.partitions[i][k1] != v[k1]:
                        self.interface.change('partition', self.partitions[i]['label'], k1, v[k1])

                self.partitions[i].update(v)
                i += 1

        elif message.fields.value.address == 2:
            i = 1
            while i <= ZONES and i in message.fields.value.zone_status:
                v = message.fields.value.zone_status[i]
                for k1,v1 in v.items():
                    if k1 not in self.zones[i] or self.zones[i][k1] != v[k1]:
                        self.interface.change('zone', self.zones[i]['label'], k1, v[k1])
                self.zones[i].update(v)
                i+=1

    def disconnect(self):
        pass

