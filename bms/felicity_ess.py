# -*- coding: utf-8 -*-

# NOTES
# Please see "Add/Request a new BMS" https://louisvdw.github.io/dbus-serialbattery/general/supported-bms#add-by-opening-a-pull-request
# in the documentation for a checklist what you have to do, when adding a new BMS

# avoid importing wildcards
from battery import Protection, Battery, Cell, History
from utils import is_bit_set, read_serial_data, logger
import utils
from typing import Union
from struct import unpack_from
import sys

import ext.minimalmodbus as minimalmodbus
import serial

RETRYCNT = 3

class FelicityEss(Battery):
    def __init__(self, port, baud, address):
        super(FelicityEss, self).__init__(port, baud, address)
        self.type = self.BATTERYTYPE
        self.serialnumber = ""
        self.cell_count = 16
        self.temp_count = 8
        self.cells = []
        self.temps = []
        self.bms_temp = 0
        self.voltage = 0
        self.soc = 0
        self.bat_status = 0
        self.bat_fault_status = 0
        self.hardware_version = "noHwVer - driver by: bc_engineering"
        self.capacity = utils.BATTERY_CAPACITY
        self.mbdev: Union[minimalmodbus.Instrument, None] = None
        if address is not None and len(address) > 0:
            self.slaveaddress: int = int(address)
        else:
            self.slaveaddress: int = 1
        self.firmwareVersion = 0
        self.protection = Protection()
        self.history = History()
        self.charge_fet = None
        self.discharge_fet = None
        self.control_allow_charge = None
        self.control_allow_discharge = None
        self.get_settings()

    BATTERYTYPE = "Felicity_ESS_modbus"
    LENGTH_CHECK = 4
    LENGTH_POS = 3

    def get_modbus(self, slaveaddress=0) -> minimalmodbus.Instrument:
        # hack to allow communication to the Seplos BMS using minimodbus which uses slaveaddress 0 as broadcast
        # Make sure we re-set these values whenever we want to access the modbus. Just in case of a
        # multi-device setup with different addresses and subsequent tries on a different address modified it.
        if slaveaddress == 0:
            minimalmodbus._SLAVEADDRESS_BROADCAST = 0xF0
        else:
            minimalmodbus._SLAVEADDRESS_BROADCAST = 0

        if self.mbdev is not None and slaveaddress == self.slaveaddress:
            return self.mbdev

        mbdev = minimalmodbus.Instrument(
            self.port,
            slaveaddress=slaveaddress,
            mode="rtu",
            close_port_after_each_call=True,
            debug=False,
        )
        mbdev.serial.parity = minimalmodbus.serial.PARITY_NONE
        mbdev.serial.stopbits = serial.STOPBITS_ONE
        mbdev.serial.baudrate = 9600
        mbdev.serial.timeout = 0.4
        return mbdev

    def test_connection(self):
        # call a function that will connect to the battery, send a command and retrieve the result.
        # The result or call should be unique to this BMS. Battery name or version, etc.
        # Return True if success, False for failure

        # This will cycle through all the slave addresses to find the BMS.

        logger.info(
            f"Start testing for Felicity_ESS on slave address {self.slaveaddress}"
        )
        found = False
        for n in range(1, RETRYCNT):
            try:
                mb = self.get_modbus(self.slaveaddress)
                self.firmwareVersion = mb.read_registers(
                    registeraddress=0xF80B, number_of_registers=0x01, functioncode=3
                )
                found = True
                logger.info(
                    f"Felicity_ESS firmware version {self.firmwareVersion}"
                )

            except Exception as e:
                logger.debug(
                    f"Felicity_ESS testing failed ({e}) {n}/{RETRYCNT} for {self.port}({str(self.slaveaddress)})"
                )
                continue
            break


        # give the user a feedback that no BMS was found
        if not found:
            logger.error(">>> ERROR: No Felicity_ESS found - returning")

        return found

    def unique_identifier(self) -> str:
        """
        Used to identify a BMS when multiple BMS are connected
        Provide a unique identifier from the BMS to identify a BMS, if multiple same BMS are connected
        e.g. the serial number
        If there is no such value, please remove this function
        """
        return self.slaveaddress

    def get_settings(self):
        # After successful  connection get_settings will be call to set up the battery.
        # Set the current limits, populate cell count, etc
        # Return True if success, False for failure

        # initialize cell array
        cnt = 1
        while cnt <= self.cell_count:
            self.cells.append(Cell(False))
            cnt += 1
        
        # initialize temp array
        cnt = 1
        while cnt <= self.temp_count:
            self.temps.append(-10) # init -10degC --> no charge / discharge
            cnt += 1

        # initialize single temps
        self.temp1: float = None
        self.temp2: float = None
        self.temp3: float = None
        self.temp4: float = None

        # call once data from BMS to get defaults
        return self.refresh_data()

    def refresh_data(self):
        # call all functions that will refresh the battery ist.
        # This will be called for every iteration (1 second)
        # Return True if success, False for failure
        logger.debug(f"Felicity_ESS: refresh_data() enter function")
        # modbus object
        mb = self.get_modbus(self.slaveaddress)

        # collect cell ist
        cellDataValidity = self.readCellData(mb)

        # collect charge / discharge limits
        limitsValidity = self.readLimits(mb)

        # collect battery infos
        infoValidity = self.readBatInfo(mb)

        # set needed battery protection
        self.processBatteryProtection()

        logger.debug(f"Felicity_ESS: refresh_data() cellDataValidity="+ str(cellDataValidity) + ";" +"limitsValidity="+ str(limitsValidity) + ";"+"infoValidity="+ str(infoValidity))

        return (cellDataValidity and limitsValidity and infoValidity) #return True in case connection is established and ist is valid


    def readCellData(self,mb):
        #collect cell ist
        try: 
            dataList = mb.read_registers(registeraddress=0x132A, number_of_registers=0x18, functioncode=3)
            logger.debug(f"Felicity_ESS: readCellData() reponse {dataList}")

            if len(dataList) >= 24:
                
                cnt = 0
                tmpVoltage = 0
                for ist in dataList:
                    # first 16 entries are cell voltage in mV
                    if cnt <= self.cell_count - 1: #cellvoltages
                        self.cells[cnt].voltage = ist / 1000 # mV to V
                        tmpVoltage = tmpVoltage + self.cells[cnt].voltage
                        logger.debug(f"Felicity_ESS: readCellData() cell voltage " + str(cnt) + " " + str(ist) + "mV")
                    # 17-24 ciontaining temperature informations (temp 5-8 seems to be not connected/supported)
                    elif cnt <= 24 - 1:
                        self.temps[cnt-self.cell_count] = ist
                        logger.debug(f"Felicity_ESS: readCellData() temp " + str(cnt-16) + " " + str(ist) + "degC")
                    cnt += 1
                
                # assign to temps
                self.temp1 = self.temps[0]
                self.temp2 = self.temps[1]
                self.temp3 = self.temps[2]
                self.temp4 = self.temps[3]
                logger.debug(f"Felicity_ESS: readCellData() tmp bat voltage " + str(tmpVoltage) + "V")
                return True
            else:
                logger.debug(
                    f"Felicity_ESS: readCellData() return ist length < 24 --> " + str(len(dataList))
                )
                return False
        
        except Exception as e:
            logger.debug(f"Felicity_ESS: readCellData() failed to request cell voltages / temps ({e})")

            return False
        
    def readLimits(self,mb):
        #collect limits
        try: 
            dataList = mb.read_registers(registeraddress=0x131C, number_of_registers=0x04, functioncode=3)
            logger.debug(f"Felicity_ESS: readLimits() reponse {dataList}")

            if len(dataList) >= 4:
                # 0 is charge voltage limit in 0.01V
                # 1 is discharge charge voltage limit in 0.01V
                # 2 is charge current limit in 0.1A
                # 3 is charge discharge current limit in 0.1A
                maxVoltBMS = dataList[0] / 100
                if (maxVoltBMS > utils.MAX_CELL_VOLTAGE * self.cell_count):
                    self.max_battery_voltage = utils.MAX_CELL_VOLTAGE * self.cell_count
                else:
                    self.max_battery_voltage = maxVoltBMS
                logger.debug(f"Felicity_ESS: readLimits() self.max_battery_voltage: " + str(self.max_battery_voltage))

                minVoltBMS = dataList[1] / 100
                if (minVoltBMS < utils.MIN_CELL_VOLTAGE * self.cell_count):
                    self.min_battery_voltage = utils.MIN_CELL_VOLTAGE * self.cell_count
                else:
                    self.min_battery_voltage = minVoltBMS
                logger.debug(f"Felicity_ESS: readLimits() self.min_battery_voltage: " + str(self.min_battery_voltage))

                maxChrgCurBMS = dataList[2] / 10
                if (maxChrgCurBMS > utils.MAX_BATTERY_CHARGE_CURRENT):
                    self.max_battery_charge_current = utils.MAX_BATTERY_CHARGE_CURRENT
                else:
                    self.max_battery_charge_current = maxChrgCurBMS
                logger.debug(f"Felicity_ESS: readLimits() self.max_battery_charge_current: " + str(self.max_battery_charge_current))

                maxDischrgCurBMS = dataList[3] / 10
                if (maxDischrgCurBMS > utils.MAX_BATTERY_DISCHARGE_CURRENT):
                    self.max_battery_discharge_current = utils.MAX_BATTERY_DISCHARGE_CURRENT
                else:
                    self.max_battery_discharge_current = maxDischrgCurBMS
                logger.debug(f"Felicity_ESS: readLimits() self.max_battery_discharge_current: " + str(self.max_battery_discharge_current))

                return True
            else:
                logger.debug(f"Felicity_ESS: readLimits() return ist length < 4 --> " + str(len(dataList)))
                return False
        
        except Exception as e:
            logger.debug(
                f"Felicity_ESS: readCellData() failed to request min/max voltage / current ({e})"
            )

            return False
        
    def readBatInfo(self,mb):
        #collect bat info
        try: 
            dataList = mb.read_registers(registeraddress=0x1302, number_of_registers=0x0A, functioncode=3)
            logger.debug(f"Felicity_ESS: readBatInfo() reponse {dataList}")

            if len(dataList) >= 10:
                # 0 Bttery Status
                # 1 None
                # 2 Fault Status
                # 3 None
                # 4 Battery Voltage in 0.01V
                # 5 Battery Current in 0.1A
                # 6 None
                # 7 None
                # 8 BMS Temp in degC
                # 9 SOC in %
                self.soc = dataList[9]
                logger.debug(f"Felicity_ESS: readBatInfo() self.soc " + str(self.soc))

                self.bms_temp = dataList[8]
                logger.debug(f"Felicity_ESS: readBatInfo() self.bms_temp " + str(self.bms_temp))

                self.temp_mos = self.bms_temp
                logger.debug(f"Felicity_ESS: readBatInfo() self.temp_mos " + str(self.temp_mos))

                self.bat_status = dataList[0]
                logger.debug(f"Felicity_ESS: readBatInfo() self.bat_status " + str(self.bat_status))

                self.bat_fault_status = dataList[2]
                logger.debug(f"Felicity_ESS: readBatInfo() self.bat_fault_status " + str(self.bat_fault_status))

                self.voltage = dataList[4] / 100
                logger.debug(f"Felicity_ESS: readBatInfo() self.voltage " + str(self.voltage))

                self.current = dataList[5] / 10                
                logger.debug(f"Felicity_ESS: readBatInfo() self.current " + str(self.current))

                return True
            else:
                logger.debug(f"Felicity_ESS: readBatInfo() return ist length < 4 --> " + str(len(dataList)))
                return False
        
        except Exception as e:
            logger.debug(
                f"Felicity_ESS: readBatInfo() failed to request battery informations ({e})"
            )

            return False
        
    def processBatteryProtection(self):
        # check general status (allow and error)
        self.charge_fet = self.isBitSet(self.bat_status,0) and not self.isBitSet(self.bat_fault_status,2)
        self.control_allow_charge = self.isBitSet(self.bat_status,0) and not self.isBitSet(self.bat_fault_status,2)

        self.discharge_fet = self.isBitSet(self.bat_status,2) and not self.isBitSet(self.bat_fault_status,3)
        self.control_allow_discharge = self.isBitSet(self.bat_status,2) and not self.isBitSet(self.bat_fault_status,3)


        # disable charge / discharge based on battery current limits
        if (self.max_battery_charge_current <= 0):
            self.charge_fet = False
            self.control_allow_charge = False

        if (self.max_battery_discharge_current <= 0):
            self.discharge_fet = False
            self.control_allow_discharge = False


        # reset protections
        self.protection.high_voltage = Protection.OK if self.voltage < self.max_battery_voltage else Protection.ALARM
        self.protection.high_cell_voltage = Protection.OK if self.isBitSet(self.bat_fault_status,2) == False else Protection.ALARM
        self.protection.low_voltage = Protection.OK if self.voltage > self.min_battery_voltage else Protection.ALARM
        self.protection.low_cell_voltage = Protection.OK if self.isBitSet(self.bat_fault_status,3) == False else Protection.ALARM
        if self.soc < 10:
            self.protection.low_soc = Protection.WARNING
        elif self.soc <= 0:
            self.protection.low_soc = Protection.ALARM
        else:
            self.protection.low_soc = Protection.OK
        self.protection.high_charge_current = Protection.OK if self.isBitSet(self.bat_fault_status,4) == False else Protection.ALARM
        self.protection.high_discharge_current = Protection.OK if self.isBitSet(self.bat_fault_status,5) == False else Protection.ALARM
        self.protection.cell_imbalance = None
        self.protection.internal_failure = Protection.OK if self.bat_fault_status == 0 else Protection.ALARM
        self.protection.high_charge_temp = Protection.OK if self.isBitSet(self.bat_fault_status,8) == False else Protection.ALARM
        self.protection.low_charge_temp = Protection.OK if self.isBitSet(self.bat_fault_status,9) == False else Protection.ALARM
        self.protection.high_temperature = Protection.OK if self.isBitSet(self.bat_fault_status,8) == False else Protection.ALARM
        self.protection.low_temperature = Protection.OK if self.isBitSet(self.bat_fault_status,9) == False else Protection.ALARM
        self.protection.high_internal_temp = Protection.OK if self.isBitSet(self.bat_fault_status,6) == False else Protection.ALARM
        self.protection.fuse_blown = None

        # check if any error is present
        if self.bat_fault_status > 0:
            logger.error(f"Felicity_ESS: processBatteryProtection() error detected, diable battery operation!")
            logger.error(f"Felicity_ESS: processBatteryProtection() Cell voltage high status Error: " + str(self.isBitSet(self.bat_fault_status,2)))
            logger.error(f"Felicity_ESS: processBatteryProtection() Cell voltage low status Error: " + str(self.isBitSet(self.bat_fault_status,3)))
            logger.error(f"Felicity_ESS: processBatteryProtection() Charge current high status Error: " + str(self.isBitSet(self.bat_fault_status,4)))
            logger.error(f"Felicity_ESS: processBatteryProtection() Discharge current high status Error: " + str(self.isBitSet(self.bat_fault_status,5)))
            logger.error(f"Felicity_ESS: processBatteryProtection() BMS Temperature high status Error: " + str(self.isBitSet(self.bat_fault_status,6)))
            logger.error(f"Felicity_ESS: processBatteryProtection() Cell Temperature high status Error: " + str(self.isBitSet(self.bat_fault_status,8)))
            logger.error(f"Felicity_ESS: processBatteryProtection() Cell Temperature low status Error: " + str(self.isBitSet(self.bat_fault_status,9)))

            # additional overtemp shutoff
            if (self.isBitSet(self.bat_fault_status,6) or self.isBitSet(self.bat_fault_status,8) or self.isBitSet(self.bat_fault_status,9)): 
                self.discharge_fet = False
                self.control_allow_discharge = False
                self.charge_fet = False
                self.control_allow_charge = False



    def isBitSet(self,n, k):
        if n & (1 << k):
            return True
        else:
            return False
# ToDo:
#  - charge immediately? --> limit min SOC to 5% in VenusOS ESS-Settings

