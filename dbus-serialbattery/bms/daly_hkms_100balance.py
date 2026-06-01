# -*- coding: utf-8 -*-

# known limitations:
#   - only BMS variants with 2 cell temperature sensors supported
#   - this is TEST code, it needs further validation, your recommendations are welcome

from battery import Battery, Cell
from utils import (
    # bytearray_to_string,
    get_connection_error_message,
    # open_serial_port,
    logger,
    AUTO_RESET_SOC,
    BATTERY_CAPACITY,
    # INVERT_CURRENT_MEASUREMENT,
    # MIN_CELL_VOLTAGE,
    SOC_CALCULATION,
)
import serial
import time
import ext.minimalmodbus_mod_daly as minimalmodbus
from typing import Dict
import threading

import ctypes

c_uint8 = ctypes.c_uint8
c_bool = ctypes.c_bool

# heavily inspired by below MIT code, ported to python
# https://github.com/patagonaa/esphome-daly-hkms-bms/blob/main/components/daly_hkms_bms/daly_hkms_bms.h


class DALY_ERROR_FLAGS_bits(ctypes.LittleEndianStructure):
    _fields_ = [
        # Code 0-1 registers
        ("lvl_cell_ovp", c_uint8, 3),
        ("lvl_cell_uvp", c_uint8, 3),
        ("smart_charger_connected", c_bool, 1),
        ("err_smart_charger_connection", c_bool, 1),
        ("lvl_cell_volt_diff", c_uint8, 3),
        ("lvl_chg_overtemp", c_uint8, 3),
        ("smart_discharger_connected", c_bool, 1),
        ("err_smart_discharger_connection", c_bool, 1),
        # Code 2-3 registers
        ("lvl_chg_undertemp", c_uint8, 3),
        ("lvl_dschg_overtemp", c_uint8, 3),
        ("err_chg_mos_temp_high", c_bool, 1),
        ("err_chg_mos_temp_detect", c_bool, 1),
        ("lvl_dschg_undertemp", c_uint8, 3),
        ("lvl_temp_diff", c_uint8, 3),
        ("err_dschg_mos_temp_high", c_bool, 1),
        ("err_dschg_mos_temp_detect", c_bool, 1),
        # Code 4-5 registers
        ("lvl_total_ovp", c_uint8, 3),
        ("lvl_total_uvp", c_uint8, 3),
        ("err_short_circuit", c_bool, 1),
        ("upgrade_sign", c_bool, 1),
        ("lvl_chg_ocp", c_uint8, 3),
        ("lvl_dschg_ocp", c_uint8, 3),
        ("err_chg_undervoltage", c_bool, 1),
        ("err_dschg_overvoltage", c_bool, 1),
        # Code 6-7 registers
        ("lvl_soc_low", c_uint8, 3),
        ("lvl_soh_low", c_uint8, 3),
        ("parallel_comm", c_bool, 1),
        ("err_parallel_comm", c_bool, 1),
        ("lvl_mos_overtemp", c_uint8, 3),
        ("lvl_thermal_runaway", c_uint8, 3),
        ("unnamed1", c_bool, 1),
        ("unnamed2", c_bool, 1),
        # Code 8-9 registers
        ("unnamed3", c_uint8, 8),
        ("unnamed4", c_uint8, 8),
        # Code 10-11 registers
        ("unnamed5", c_uint8, 8),
        ("err_afe_chip", c_bool, 1),
        ("err_afe_comm", c_bool, 1),
        ("err_afe_sampling", c_bool, 1),
        ("err_volt_detect", c_bool, 1),
        ("err_volt_detect_disconnected", c_bool, 1),
        ("err_volt_total_detect", c_bool, 1),
        ("err_curr_detect", c_bool, 1),
        ("err_temp_detect", c_bool, 1),
        # Code 12-13 registers
        ("err_temp_disconnected", c_bool, 1),
        ("err_eeprom", c_bool, 1),
        ("err_flash", c_bool, 1),
        ("err_rtc", c_bool, 1),
        ("err_chg_mos", c_bool, 1),
        ("err_dschg_mos", c_bool, 1),
        ("err_prechg_mos", c_bool, 1),
        ("err_prechg", c_bool, 1),
        ("chg_mos_off_bus", c_bool, 1),
        ("dschg_mos_off_bus", c_bool, 1),
        ("chg_mos_off_switch", c_bool, 1),
        ("dschg_mos_off_switch", c_bool, 1),
        ("fan_active", c_bool, 1),
        ("heating_active", c_bool, 1),
        ("current_limit_active", c_bool, 1),
        ("err_heating", c_bool, 1),
    ]


class DALY_ERROR_FLAGS_registers(ctypes.LittleEndianStructure):
    _fields_ = [
        # Code 0-1 registers
        ("ERR_00_01", ctypes.c_uint16, 16),
        ("ERR_02_03", ctypes.c_uint16, 16),
        ("ERR_04_05", ctypes.c_uint16, 16),
        ("ERR_06_07", ctypes.c_uint16, 16),
        ("ERR_08_09", ctypes.c_uint16, 16),
        ("ERR_10_11", ctypes.c_uint16, 16),
        ("ERR_12_13", ctypes.c_uint16, 16),
    ]


class DALY_ERROR_FLAGS_type(ctypes.Union):
    _fields_ = [("b", DALY_ERROR_FLAGS_bits), ("regs", DALY_ERROR_FLAGS_registers)]


# register addresses, from source below MIT, extended
# https://github.com/patagonaa/esphome-daly-hkms-bms/blob/main/components/daly_hkms_bms/daly_hkms_bms_registers.h

# 0x00 - 0x2F
DALY_MODBUS_ADDR_CELL_VOLT_1 = 0x00

# 0x30 - 0x37
DALY_MODBUS_ADDR_CELL_TEMP_1 = 0x30

DALY_MODBUS_ADDR_VOLT = 0x38
DALY_MODBUS_ADDR_CURR = 0x39
DALY_MODBUS_ADDR_SOC = 0x3A
DALY_MODBUS_ADDR_SOH = 0x3B

DALY_MODBUS_ADDR_CELL_COUNT = 0x3C
DALY_MODBUS_ADDR_CELL_TEMP_COUNT = 0x3D
DALY_MODBUS_ADDR_CELL_VOLT_MAX = 0x3E
DALY_MODBUS_ADDR_CELL_VOLT_MAX_NUM = 0x3F
DALY_MODBUS_ADDR_CELL_VOLT_MIN = 0x40
DALY_MODBUS_ADDR_CELL_VOLT_MIN_NUM = 0x41
DALY_MODBUS_ADDR_CELL_VOLT_DIFF = 0x42
DALY_MODBUS_ADDR_CELL_TEMP_MAX = 0x43
DALY_MODBUS_ADDR_CELL_TEMP_MAX_NUM = 0x44
DALY_MODBUS_ADDR_CELL_TEMP_MIN = 0x45
DALY_MODBUS_ADDR_CELL_TEMP_MIN_NUM = 0x46
DALY_MODBUS_ADDR_CELL_TEMP_DIFF = 0x47

DALY_MODBUS_ADDR_CHG_DSCHG_STATUS = 0x48
DALY_MODBUS_ADDR_REMAINING_CAPACITY = 0x4B
DALY_MODBUS_ADDR_CYCLES = 0x4C
DALY_MODBUS_ADDR_BALANCE_STATUS = 0x4D
DALY_MODBUS_ADDR_BALANCE_CURRENT = 0x4E
DALY_MODBUS_ADDR_BALANCE_STATUS_PER_CELL_01_TO_16 = 0x4F
DALY_MODBUS_ADDR_BALANCE_STATUS_PER_CELL_17_TO_32 = 0x50
DALY_MODBUS_ADDR_BALANCE_STATUS_PER_CELL_33_TO_48 = 0x51

DALY_MODBUS_ADDR_CHG_MOS_ACTIVE = 0x52
DALY_MODBUS_ADDR_DSCHG_MOS_ACTIVE = 0x53
DALY_MODBUS_ADDR_PRECHG_MOS_ACTIVE = 0x54
DALY_MODBUS_ADDR_HEATING_MOS_ACTIVE = 0x55
DALY_MODBUS_ADDR_FAN_MOS_ACTIVE = 0x56

DALY_MODBUS_ADDR_POWER = 0x58  # has to be in the same message as 0x48
DALY_MODBUS_ADDR_ENERGY = 0x59

DALY_MODBUS_ADDR_MOS_TEMP = 0x5A
DALY_MODBUS_ADDR_BOARD_TEMP = 0x5B
DALY_MODBUS_ADDR_HEATING_TEMP = 0x5C

DALY_MODBUS_ADDR_REMAINING_MILEAGE = 0x5E
DALY_MODBUS_ADDR_REMAINING_CHARGING_TIME = 0x64

# 0x66 - 0x69
DALY_MODBUS_ADDR_BMS_TYPE_1_ERR_1 = 0x66

# 0x6D - 0x73
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_00_01 = 0x6D
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_02_03 = 0x6E
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_04_05 = 0x6F
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_06_07 = 0x70
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_08_09 = 0x71
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_10_11 = 0x72
DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_12_13 = 0x73

DALY_MODBUS_ADDR_SET_SOC = 0x116
DALY_MODBUS_ADDR_CHG_MOS_CONTROL = 0x121
DALY_MODBUS_ADDR_DSCHG_MOS_CONTROL = 0x122

DALY_MODBUS_ADDR_CAPACITIES = 0x0109
DALY_MODBUS_ADDR_TOTAL_AH_CHARGED = 0x10D
DALY_MODBUS_ADDR_TOTAL_AH_DISCHARGED = 0x10F
DALY_MODBUS_ADDR_PRODUCTION_DATE = 0x0129
DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_1 = 0x0140
DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_2 = 0x0141
DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_2_DELAY = 0x0142
# 81 06 01 42 04 D2 B5 7F 51 06 01 42 04 D2 A6 EF set overcurrent limit delay 2 to 1234ms (0x04D2)
DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_3 = 0x0143
DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_3_DELAY = 0x0144
# 81 06 01 44 15 38 D8 A1 51 06 01 44 15 38 CB 31 set overcurrent limit delay 3 to 5432ms (0x1538)
DALY_MODBUS_ADDR_DSCHG_OVERCURRENT_LIMIT_1 = 0x0145
# 81 06 01 45 75 B1 60 C7 51 06 01 45 75 B1 73 57 set discharge overcurrent alarm level 1 to 12.9A (0x75B1 == 30129 --> 30129 - 30000 = 129 --> 12.9A)
DALY_MODBUS_ADDR_DSCHG_OVERCURRENT_LIMIT_2 = 0x0146
DALY_MODBUS_ADDR_DSCHG_OVERCURRENT_LIMIT_2_DELAY = 0x0147
# 81 06 01 47 30 39 F3 F1 51 06 01 47 30 39 E0 61 set discharge overcurrent alarm level 2 delay to 12345ms (0x3039 == 12345) on device number 1
# in BMS Tool v1.14.23 (Addr(Bms): Addr_01 == 0x51 in response modbus and 0x81 in request modbus)
DALY_MODBUS_ADDR_DSCHG_OVERCURRENT_LIMIT_3 = 0x0148
# 1 16bit register wide, value is 30000 + current limit in 0.1A steps, so for e.g. 18.9A set value to 30183 (0x75E7) written to bus with in order 0x75 0xE7
DALY_MODBUS_ADDR_DSCHG_OVERCURRENT_LIMIT_3_DELAY = 0x0149
# 1 16bit reg, value is integer in ms
# 81 06 01 49 7A 0D A4 85 51 06 01 49 7A 0D B7 15 set discharge overcurrent alarm level 3 delay to 31245ms (0x7A0D == 31245)

DALY_MODBUS_ADDR_SW_HW_VER = 0x0178

# #end of imported / ported code

# #recorded exchanges with bms software and daly 100 blance bms
# 82 06 01 22 00 00 37 CF set discharge mos to off
# 81 10 01 09 00 02 00 00 59 D8 F0 EC 51 10 01 09 00 02 9C 66 set rated capacity
# 81 10 01 0B 00 02 00 00 2A F8 F7 C4 51 10 01 0B 00 02 3D A6 set actual capacity
# #added FK regs found through BMS Tool-V1.14.61.2 rs485 bus listening
# #set TotalAh of Charge to 33.33333333
# #81 10 01 0D 00 02 00 00 82 35 2F 91 <-request | response-> 51 10 01 0D 00 02 DD A7
# #0x010D address: datalen: 0x0002 regs 0x0000 0x8235 uint32_t --> UINT32 - Big Endian (ABCD) 33333
# #set TotalAh of Dischrarge to 4567.123456
# #81 10 01 0F 00 02 00 45 B0 53 88 CE <-request | response->  51 10 01 0F 00 02 7C 67
# #0x010F address: datalen: 0x0002 regs 0x0045 0xB053 uint32_t --> UINT32 - Big Endian (ABCD) 4567123
# battery production date
# set with bms tool v1.14.23 zu 2025 12 05
# 81 10 01 29 00 02 19 0C 05 00 2F 2B set command, response: 51 10 01 29 00 02 9D AC
# addr functioncode addr reg 2 regs checksum
# reg addr: 0x0129, reg len: 02, regs: 0x190C 0x 05 00
# ==> 0x0005190C
# 00 05 -> day
# 0x19 == 25 -> year
# 0x0C == 12 -> month

# the Heltec BMS is not always as responsive as it should, so let's try it up to (RETRYCNT - 1) times to talk to it
RETRYCNT = 4

# the wait time after a communication - normally this should be as defined by modbus RTU and handled in minimalmodbus,
# but yeah, it seems we need it for the Heltec BMS
SLPTIME = 0.03

mbdevs: Dict[int, minimalmodbus.Instrument] = {}
locks: Dict[int, any] = {}


class Daly_HKMS_100balance(Battery):
    def __init__(self, port, baud, address):
        super(Daly_HKMS_100balance, self).__init__(port, baud, address)
        self.type = "Daly_HKMS_100balance"
        self.unique_identifier_tmp = ""
        self.capacity = BATTERY_CAPACITY
        self.Daly_HKMS_100balance_communtication_error_count: int = 0  # this is a debug counter to judge reliability from the current logfile only
        self.Daly_HKMS_100balance_communtication_start_time: float = time.time()
        self.Daly_HKMS_100balance_communtication_error_last_error_time: float = 0
        self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start: int = 0
        self.has_settings = True
        self.reset_soc = 0
        self.soc_to_set = None
        self.last_charge_mode = self.charge_mode
        self.available_callbacks = [
            "callback_soc_reset_to",
            "trigger_soc_reset",
        ]

    def test_connection(self):
        """
        call a function that will connect to the battery, send a command and retrieve the result.
        The result or call should be unique to this BMS. Battery name or version, etc.
        Return True if success, False for failure
        """
        logger.debug("Testing on slave address " + str(self.address))
        found = False
        if self.address not in locks:
            locks[self.address] = threading.Lock()

        # copied from Heltec BMS thread safety
        # TODO: We need to lock not only based on the address, but based on the port as soon as multiple BMSs
        # are supported on the same serial interface. Then locking on the port will be enough.

        with locks[self.address]:
            mbdev = minimalmodbus.Instrument(
                self.port,
                slaveaddress=int.from_bytes(
                    self.address, byteorder="big"
                ),  # use the reply address from the slave 0x52 for board number 2, modified minimalmodbus2 will add 0x52 +0x30 = 0x82 as send address
                slave_send_address_offset=0x30,  # added to slaveaddress for sending commands by modded minimalmodbus2
                mode="rtu",
                close_port_after_each_call=True,
                debug=False,
            )
            mbdev.serial.parity = minimalmodbus.serial.PARITY_NONE
            mbdev.serial.stopbits = serial.STOPBITS_ONE
            mbdev.serial.baudrate = 9600
            # yes, 400ms is long but the BMS is sometimes really slow in responding, so this is a good compromise
            mbdev.serial.timeout = 0.4
            mbdevs[self.address] = mbdev
            batcodedirect = ""
            for n in range(1, RETRYCNT):
                try:
                    batcoderegsbytearray = minimalmodbus._valuelist_to_bytes(mbdev.read_registers(DALY_MODBUS_ADDR_SW_HW_VER + 35, 16, 3), 16)
                    batcodedirect = batcoderegsbytearray.rstrip(b"\x00").decode(encoding="ascii")
                    time.sleep(SLPTIME)
                    found = True
                    logger.info("found in try " + str(n) + "/" + str(RETRYCNT) + " for " + self.port + "(" + str(self.address) + "): " + batcodedirect)
                except Exception as e:
                    logger.warning("testing failed (" + str(e) + ") " + str(n) + "/" + str(RETRYCNT) + " for " + self.port + "(" + str(self.address) + ")")
                    continue
                break

            if found:
                self.type = "#" + str(int.from_bytes(self.address, byteorder="big") - 0x50) + "_" + batcodedirect  # Daly_HKMS_100balance"

        # give the user a feedback that no BMS was found
        if not found:
            get_connection_error_message(self.online)

        logger.debug("in test_connection():")
        getset = self.get_settings()
        getdat = self.refresh_data()
        logger.debug("in test_connection(): found: " + str(found) + " get_settings: " + str(getset) + " refresh_data: " + str(getdat))
        return found and getset and getdat

    def get_settings(self):
        # After successful connection get_settings() will be called to set up the battery
        # Set the current limits, populate cell count, etc
        # Return True if success, False for failure
        mbdev = mbdevs[self.address]
        self.capacity = 1.6  # temp set dummy value, to avoid errors while retrieving actual value

        with locks[self.address]:
            try:
                cellcount = mbdev.read_register(DALY_MODBUS_ADDR_CELL_COUNT, 0, 3, False)
                self.cell_count = cellcount
                logger.debug("read cellcount from bms: " + str(cellcount))
            except Exception as e:
                logger.warning("Error reading cellcount from BMS: " + str(e))
                return False
            try:
                # read sw hw strings
                swhwvers_regs = mbdev.read_registers(DALY_MODBUS_ADDR_SW_HW_VER, 0x68, 3)
                hist_regs = mbdev.read_registers(
                    DALY_MODBUS_ADDR_TOTAL_AH_CHARGED, DALY_MODBUS_ADDR_TOTAL_AH_DISCHARGED - DALY_MODBUS_ADDR_TOTAL_AH_CHARGED + 2, 3
                )
                capacityregs = mbdev.read_registers(DALY_MODBUS_ADDR_CAPACITIES, 4, 3)
                productiondateregs = mbdev.read_registers(DALY_MODBUS_ADDR_PRODUCTION_DATE, 0x02, 3)
                currentlimit_regs = mbdev.read_registers(
                    DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_1, DALY_MODBUS_ADDR_DSCHG_OVERCURRENT_LIMIT_3_DELAY - DALY_MODBUS_ADDR_CHG_OVERCURRENT_LIMIT_1 + 1, 3
                )
                # convert and set
                swhwvers_bytes = minimalmodbus._valuelist_to_bytes(swhwvers_regs, 0x68)
                hwverA = swhwvers_bytes[0:14].rstrip(b"\x00").decode(encoding="ascii")
                swver = swhwvers_bytes[14:28].rstrip(b"\x00").decode(encoding="ascii")
                hwverB = swhwvers_bytes[28:42].rstrip(b"\x00").decode(encoding="ascii")
                serno = swhwvers_bytes[42:70].rstrip(b"\x00").decode(encoding="ascii")
                batcode = swhwvers_bytes[70:102].rstrip(b"\x00").decode(encoding="ascii")
                logger.debug(
                    "read version strings from bms: batcode: "
                    + batcode
                    + " hwversionA: "
                    + hwverA
                    + " hwversionB: "
                    + hwverB
                    + " swversion: "
                    + swver
                    + " bmsserialnumber: "
                    + serno
                    + " len serno: "
                    + str(len(serno))
                )
                self.unique_identifier_tmp = serno
                self.custom_field = batcode + ":" + swver
                productiondatebytes = minimalmodbus._valuelist_to_bytes(productiondateregs, 0x02)
                logger.debug(
                    "day: " + str(int(productiondatebytes[2])) + " month: " + str(int(productiondatebytes[1])) + " year: " + str(int(productiondatebytes[0]))
                )
                self.production = (
                    "day: " + str(int(productiondatebytes[2])) + " month: " + str(int(productiondatebytes[1])) + " year: " + str(int(productiondatebytes[0]))
                )
                self.hardware_version = hwverA + ":" + hwverB
                # set to rated capacity
                self.capacity = (capacityregs[1] + capacityregs[0] * 65536) / 1000
                # totalahcharged    = (hist_regs[0]*65536+hist_regs[1])/1000
                totalahdischarged = (hist_regs[2] * 65536 + hist_regs[3]) / 1000
                # logger.debug("totalahcharged: " + str(totalahcharged) + " totalahdischarged: " + str(totalahdischarged))
                self.history.total_ah_drawn = totalahdischarged
                # current limits, set Alarm level 1 correctly, the pre warning level in the BMS Tool PC program, the BMS App for phones only sets
                # the Alarm level 2, which disables the corresponding mosfet. This code uses the Alarm level 1 as limit.
                # Alternitavely use MAX_BATTERY_CHARGE_CURRENT and MAX_BATTERY_DISCHARGE_CURRENT in config.ini to set another limit.
                logger.debug("currentlimit_regs[0]: " + str(currentlimit_regs[0]) + " currentlimit_regs[5]: " + str(currentlimit_regs[5]))
                self.max_battery_charge_current = (30000 - currentlimit_regs[0]) / 10
                self.max_battery_discharge_current = (currentlimit_regs[5] - 30000) / 10

                self.Daly_HKMS_100balance_communtication_start_time = time.time()
            except Exception as e:
                logger.warning("Error reading sw hw version strings and battcode from BMS: " + str(e))
                return False
        return True

    def daly_check_if_any_err_active(self, errors: DALY_ERROR_FLAGS_type) -> bool:
        erractive: bool = False
        erractive = erractive if (errors.regs.ERR_00_01 == 0) else True
        erractive = erractive if (errors.regs.ERR_02_03 == 0) else True
        erractive = erractive if (errors.regs.ERR_04_05 == 0) else True
        erractive = erractive if (errors.regs.ERR_06_07 == 0) else True
        erractive = erractive if (errors.regs.ERR_08_09 == 0) else True
        erractive = erractive if (errors.regs.ERR_10_11 == 0) else True
        erractive = erractive if (errors.regs.ERR_12_13 == 0) else True
        return erractive

    def daly_pretty_print_all_errors(self, errors: DALY_ERROR_FLAGS_type):
        logger.debug("______________ some errors active ______ complete error list: (" + self.unique_identifier_tmp + ")")
        if errors.b.lvl_cell_ovp != 0:
            logger.debug("lvl_cell_ovp: " + str(errors.b.lvl_cell_ovp))
        if errors.b.lvl_cell_uvp != 0:
            logger.debug("lvl_cell_uvp: " + str(errors.b.lvl_cell_uvp))
        # if errors.b.smart_charger_connected != 0:
        #     logger.debug("smart_charger_connected: " + str(errors.b.smart_charger_connected))
        # if errors.b.err_smart_charger_connection != 0:
        #     logger.debug("err_smart_charger_connection: " + str(errors.b.err_smart_charger_connection))
        if errors.b.lvl_cell_volt_diff != 0:
            logger.debug("lvl_cell_volt_diff: " + str(errors.b.lvl_cell_volt_diff))
        if errors.b.lvl_chg_overtemp != 0:
            logger.debug("lvl_chg_overtemp: " + str(errors.b.lvl_chg_overtemp))
        if errors.b.smart_discharger_connected != 0:
            logger.debug("smart_discharger_connected: " + str(errors.b.smart_discharger_connected))
        if errors.b.err_smart_discharger_connection != 0:
            logger.debug("err_smart_discharger_connection: " + str(errors.b.err_smart_discharger_connection))
        if errors.b.lvl_chg_undertemp != 0:
            logger.debug("lvl_chg_undertemp: " + str(errors.b.lvl_chg_undertemp))
        if errors.b.lvl_dschg_overtemp != 0:
            logger.debug("lvl_dschg_overtemp: " + str(errors.b.lvl_dschg_overtemp))
        if errors.b.err_chg_mos_temp_high != 0:
            logger.debug("err_chg_mos_temp_high: " + str(errors.b.err_chg_mos_temp_high))
        if errors.b.err_chg_mos_temp_detect != 0:
            logger.debug("err_chg_mos_temp_detect: " + str(errors.b.err_chg_mos_temp_detect))
        if errors.b.lvl_dschg_undertemp != 0:
            logger.debug("lvl_dschg_undertemp: " + str(errors.b.lvl_dschg_undertemp))
        if errors.b.lvl_temp_diff != 0:
            logger.debug("lvl_temp_diff: " + str(errors.b.lvl_temp_diff))
        if errors.b.err_dschg_mos_temp_high != 0:
            logger.debug("err_dschg_mos_temp_high: " + str(errors.b.err_dschg_mos_temp_high))
        if errors.b.err_dschg_mos_temp_detect != 0:
            logger.debug("err_dschg_mos_temp_detect: " + str(errors.b.err_dschg_mos_temp_detect))
        if errors.b.lvl_total_ovp != 0:
            logger.debug("lvl_total_ovp: " + str(errors.b.lvl_total_ovp))
        if errors.b.lvl_total_uvp != 0:
            logger.debug("lvl_total_uvp: " + str(errors.b.lvl_total_uvp))
        if errors.b.err_short_circuit != 0:
            logger.debug("err_short_circuit: " + str(errors.b.err_short_circuit))
        if errors.b.upgrade_sign != 0:
            logger.debug("upgrade_sign: " + str(errors.b.upgrade_sign))
        if errors.b.lvl_chg_ocp != 0:
            logger.debug("lvl_chg_ocp: " + str(errors.b.lvl_chg_ocp))
        if errors.b.lvl_dschg_ocp != 0:
            logger.debug("lvl_dschg_ocp: " + str(errors.b.lvl_dschg_ocp))
        if errors.b.err_chg_undervoltage != 0:
            logger.debug("err_chg_undervoltage: " + str(errors.b.err_chg_undervoltage))
        if errors.b.err_dschg_overvoltage != 0:
            logger.debug("err_dschg_overvoltage: " + str(errors.b.err_dschg_overvoltage))
        if errors.b.lvl_soc_low != 0:
            logger.debug("lvl_soc_low: " + str(errors.b.lvl_soc_low))
        if errors.b.lvl_soh_low != 0:
            logger.debug("lvl_soh_low: " + str(errors.b.lvl_soh_low))
        if errors.b.parallel_comm != 0:
            logger.debug("parallel_comm: " + str(errors.b.parallel_comm))
        if errors.b.err_parallel_comm != 0:
            logger.debug("err_parallel_comm: " + str(errors.b.err_parallel_comm))
        if errors.b.lvl_mos_overtemp != 0:
            logger.debug("lvl_mos_overtemp: " + str(errors.b.lvl_mos_overtemp))
        if errors.b.lvl_thermal_runaway != 0:
            logger.debug("lvl_thermal_runaway: " + str(errors.b.lvl_thermal_runaway))
        if errors.b.unnamed1 != 0:
            logger.debug("unnamed1: " + str(errors.b.unnamed1))
        if errors.b.unnamed2 != 0:
            logger.debug("unnamed2: " + str(errors.b.unnamed2))
        if errors.b.unnamed3 != 0:
            logger.debug("unnamed3: " + str(errors.b.unnamed3))
        if errors.b.unnamed4 != 0:
            logger.debug("unnamed4: " + str(errors.b.unnamed4))
        if errors.b.unnamed5 != 0:
            logger.debug("unnamed5: " + str(errors.b.unnamed5))
        if errors.b.err_afe_chip != 0:
            logger.debug("err_afe_chip: " + str(errors.b.err_afe_chip))
        if errors.b.err_afe_comm != 0:
            logger.debug("err_afe_comm: " + str(errors.b.err_afe_comm))
        if errors.b.err_afe_sampling != 0:
            logger.debug("err_afe_sampling: " + str(errors.b.err_afe_sampling))
        if errors.b.err_volt_detect != 0:
            logger.debug("err_volt_detect: " + str(errors.b.err_volt_detect))
        if errors.b.err_volt_detect_disconnected != 0:
            logger.debug("err_volt_detect_disconnected: " + str(errors.b.err_volt_detect_disconnected))
        if errors.b.err_volt_total_detect != 0:
            logger.debug("err_volt_total_detect: " + str(errors.b.err_volt_total_detect))
        if errors.b.err_curr_detect != 0:
            logger.debug("err_curr_detect: " + str(errors.b.err_curr_detect))
        if errors.b.err_temp_detect != 0:
            logger.debug("err_temp_detect: " + str(errors.b.err_temp_detect))
        if errors.b.err_temp_disconnected != 0:
            logger.debug("err_temp_disconnected: " + str(errors.b.err_temp_disconnected))
        if errors.b.err_eeprom != 0:
            logger.debug("err_eeprom: " + str(errors.b.err_eeprom))
        if errors.b.err_flash != 0:
            logger.debug("err_flash: " + str(errors.b.err_flash))
        if errors.b.err_rtc != 0:
            logger.debug("err_rtc: " + str(errors.b.err_rtc))
        if errors.b.err_chg_mos != 0:
            logger.debug("err_chg_mos: " + str(errors.b.err_chg_mos))
        if errors.b.err_dschg_mos != 0:
            logger.debug("err_dschg_mos: " + str(errors.b.err_dschg_mos))
        if errors.b.err_prechg_mos != 0:
            logger.debug("err_prechg_mos: " + str(errors.b.err_prechg_mos))
        if errors.b.err_prechg != 0:
            logger.debug("err_prechg: " + str(errors.b.err_prechg))
        if errors.b.chg_mos_off_bus != 0:
            logger.debug("chg_mos_off_bus: " + str(errors.b.chg_mos_off_bus))
        if errors.b.dschg_mos_off_bus != 0:
            logger.debug("dschg_mos_off_bus: " + str(errors.b.dschg_mos_off_bus))
        if errors.b.chg_mos_off_switch != 0:
            logger.debug("chg_mos_off_switch: " + str(errors.b.chg_mos_off_switch))
        if errors.b.dschg_mos_off_switch != 0:
            logger.debug("dschg_mos_off_switch: " + str(errors.b.dschg_mos_off_switch))
        if errors.b.fan_active != 0:
            logger.debug("fan_active: " + str(errors.b.fan_active))
        if errors.b.heating_active != 0:
            logger.debug("heating_active: " + str(errors.b.heating_active))
        if errors.b.current_limit_active != 0:
            logger.debug("current_limit_active: " + str(errors.b.current_limit_active))
        if errors.b.err_heating != 0:
            logger.debug("err_heating: " + str(errors.b.err_heating))
        logger.debug("_____________end of error list __________________________")

    def get_balancing_status_for_cellno(self, allregs: list[int], cellno: int) -> int:
        if cellno < 0 or cellno > 48:
            return False
        balancereg: int = 0
        cellmask: int = 0
        if cellno <= 16:
            balancereg = allregs[DALY_MODBUS_ADDR_BALANCE_STATUS_PER_CELL_01_TO_16 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            cellmask = 1 << cellno
        elif cellno <= 32:
            balancereg = allregs[DALY_MODBUS_ADDR_BALANCE_STATUS_PER_CELL_17_TO_32 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            cellmask = 1 << (cellno - 16)
        else:  # if(cellno<=49):
            balancereg = allregs[DALY_MODBUS_ADDR_BALANCE_STATUS_PER_CELL_33_TO_48 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            cellmask = 1 << (cellno - 32)
        balancing = (balancereg & cellmask) > 0
        return balancing

    def refresh_data(self):
        # logger.debug("in refresh_data(self)")
        # call all functions that will refresh the battery data.
        # This will be called for every iteration (1 second)
        # Return True if success, False for failure
        self.reset_soc = self.soc if self.soc else 0
        mbdev = mbdevs[self.address]

        with locks[self.address]:
            try:
                # this method reads all the data in a minimal number of modbus requests, to minimize refresh time
                start_time = time.time()
                cellvoltageregs = mbdev.read_registers(DALY_MODBUS_ADDR_CELL_VOLT_1, self.cell_count, 3)
                allregsatonceB = mbdev.read_registers(DALY_MODBUS_ADDR_CELL_TEMP_1, DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_12_13 - DALY_MODBUS_ADDR_CELL_TEMP_1 + 1, 3)
                hist_regs = mbdev.read_registers(
                    DALY_MODBUS_ADDR_TOTAL_AH_CHARGED, DALY_MODBUS_ADDR_TOTAL_AH_DISCHARGED - DALY_MODBUS_ADDR_TOTAL_AH_CHARGED + 2, 3
                )
                logger.debug("refresh_dataV2compact --- %s seconds ---" % (time.time() - start_time))  # 0.319s
                timesincelasterror: float = (
                    (time.time() - self.Daly_HKMS_100balance_communtication_error_last_error_time)
                    if (self.Daly_HKMS_100balance_communtication_error_last_error_time != 0)
                    else 0
                )
                avgerrorratesincestart = (
                    ((time.time() - self.Daly_HKMS_100balance_communtication_start_time) / self.Daly_HKMS_100balance_communtication_error_count)
                    if (self.Daly_HKMS_100balance_communtication_error_count != 0)
                    else None
                )
                logger.debug(
                    "communication error statistics: time since last: "
                    + str(timesincelasterror)
                    + " average seconds between errors: "
                    + str(avgerrorratesincestart)
                    + " total error count: "
                    + str(self.Daly_HKMS_100balance_communtication_error_count)
                )
                avgsocwriterate: float = (
                    (
                        (time.time() - self.Daly_HKMS_100balance_communtication_start_time)
                        / self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start
                    )
                    if (self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start != 0)
                    else None
                )
                logger.debug(
                    f"soc writes since driver start: {self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start}, "
                    f"avg soc write seconds between writes: {avgsocwriterate}"
                )
            except Exception as e:
                self.Daly_HKMS_100balance_communtication_error_count = self.Daly_HKMS_100balance_communtication_error_count + 1
                timesincelasterror: float = (
                    (time.time() - self.Daly_HKMS_100balance_communtication_error_last_error_time)
                    if (self.Daly_HKMS_100balance_communtication_error_last_error_time != 0)
                    else 0
                )
                self.Daly_HKMS_100balance_communtication_error_last_error_time = time.time()
                avgerrorratesincestart = (
                    time.time() - self.Daly_HKMS_100balance_communtication_start_time
                ) / self.Daly_HKMS_100balance_communtication_error_count
                logger.warning(
                    "COMM ERROR: time since last: "
                    + str(timesincelasterror)
                    + " average seconds between errors: "
                    + str(avgerrorratesincestart)
                    + " total error count: "
                    + str(self.Daly_HKMS_100balance_communtication_error_count)
                )
                logger.warning("Error reading all data from BMS: " + str(e))
                return False

            totalahcharged = (hist_regs[0] * 65536 + hist_regs[1]) / 1000
            totalahdischarged = (hist_regs[2] * 65536 + hist_regs[3]) / 1000
            logger.debug("totalahcharged: " + str(totalahcharged) + " totalahdischarged: " + str(totalahdischarged))
            self.history.total_ah_drawn = totalahdischarged

            self.voltage = allregsatonceB[DALY_MODBUS_ADDR_VOLT - DALY_MODBUS_ADDR_CELL_TEMP_1] / 10
            self.current = (allregsatonceB[DALY_MODBUS_ADDR_CURR - DALY_MODBUS_ADDR_CELL_TEMP_1] - 30000) / 10

            if (allregsatonceB[DALY_MODBUS_ADDR_DSCHG_MOS_ACTIVE - DALY_MODBUS_ADDR_CELL_TEMP_1]) == 0:
                self.discharge_fet = False
            else:
                self.discharge_fet = True
            if (allregsatonceB[DALY_MODBUS_ADDR_CHG_MOS_ACTIVE - DALY_MODBUS_ADDR_CELL_TEMP_1]) == 0:
                self.charge_fet = False
            else:
                self.charge_fet = True

            self.history.charge_cycles = allregsatonceB[DALY_MODBUS_ADDR_CYCLES - DALY_MODBUS_ADDR_CELL_TEMP_1]

            # error flags in new class
            error_flags = DALY_ERROR_FLAGS_type()
            error_flags.regs.ERR_00_01 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_00_01 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            error_flags.regs.ERR_02_03 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_02_03 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            error_flags.regs.ERR_04_05 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_04_05 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            error_flags.regs.ERR_06_07 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_06_07 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            error_flags.regs.ERR_08_09 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_08_09 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            error_flags.regs.ERR_10_11 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_10_11 - DALY_MODBUS_ADDR_CELL_TEMP_1]
            error_flags.regs.ERR_12_13 = allregsatonceB[DALY_MODBUS_ADDR_BMS_TYPE_2_ERR_12_13 - DALY_MODBUS_ADDR_CELL_TEMP_1]

            self.protection.cell_imbalance = 2 if (error_flags.b.lvl_cell_volt_diff != 0) else 0
            self.protection.high_temperature = (
                2
                if (
                    error_flags.b.lvl_chg_overtemp != 0
                    or error_flags.b.lvl_dschg_overtemp != 0
                    or error_flags.b.err_dschg_mos_temp_high != 0
                    or error_flags.b.lvl_mos_overtemp != 0
                    or error_flags.b.lvl_thermal_runaway != 0
                )
                else 0
            )
            self.protection.low_temperature = 2 if (error_flags.b.lvl_dschg_undertemp != 0 or error_flags.b.lvl_chg_undertemp != 0) else 0
            self.protection.high_voltage = (
                1 if (error_flags.b.lvl_cell_ovp != 0 or error_flags.b.lvl_total_ovp != 0 or error_flags.b.err_dschg_overvoltage != 0) else 0
            )
            self.protection.high_voltage = (
                2
                if (error_flags.b.lvl_cell_ovp > 1 or error_flags.b.lvl_total_ovp > 1 or error_flags.b.err_dschg_overvoltage > 1)
                else self.protection.high_voltage
            )
            self.protection.high_cell_voltage = 2 if (error_flags.b.lvl_cell_ovp > 1) else (1 if (error_flags.b.lvl_cell_ovp != 0) else 0)
            self.protection.low_cell_voltage = 2 if (error_flags.b.lvl_cell_uvp != 0) else 0
            self.protection.low_voltage = 2 if (error_flags.b.lvl_total_uvp != 0) else 0
            self.protection.high_discharge_current = (
                2 if (error_flags.b.lvl_dschg_ocp > 1 or error_flags.b.err_short_circuit != 0) else (1 if error_flags.b.lvl_dschg_ocp != 0 else 0)
            )
            self.protection.high_charge_current = (
                2 if (error_flags.b.lvl_chg_ocp > 1 or error_flags.b.err_short_circuit != 0) else (1 if error_flags.b.lvl_chg_ocp != 0 else 0)
            )
            self.protection.high_charge_temperature = 2 if (error_flags.b.lvl_chg_overtemp != 0 or error_flags.b.err_chg_mos_temp_high != 0) else 0
            self.protection.low_charge_temperature = 2 if (error_flags.b.lvl_chg_undertemp != 0) else 0
            self.protection.high_internal_temperature = (
                2
                if (
                    error_flags.b.err_chg_mos_temp_high != 0
                    or error_flags.b.err_dschg_mos_temp_high != 0
                    or error_flags.b.lvl_mos_overtemp != 0
                    or error_flags.b.lvl_thermal_runaway != 0
                )
                else 0
            )

            if not SOC_CALCULATION:
                self.protection.low_soc = 2 if (error_flags.b.lvl_soc_low != 0) else 0
            # all other fault codes
            self.protection.internal_failure = (
                2
                if (
                    False  # error_flags.b.err_smart_charger_connection!=0
                    # or error_flags.b.lvl_cell_volt_diff!=0
                    or error_flags.b.err_smart_discharger_connection != 0
                    or error_flags.b.err_chg_mos_temp_detect != 0
                    or error_flags.b.lvl_temp_diff != 0
                    or error_flags.b.err_dschg_mos_temp_detect != 0
                    or error_flags.b.upgrade_sign != 0
                    # or error_flags.b.err_parallel_comm!=0
                    or error_flags.b.err_afe_chip != 0
                    or error_flags.b.err_afe_comm != 0
                    or error_flags.b.err_afe_sampling != 0
                    or error_flags.b.err_volt_detect != 0
                    or error_flags.b.err_volt_detect_disconnected != 0
                    or error_flags.b.err_volt_total_detect != 0
                    or error_flags.b.err_curr_detect != 0
                    or error_flags.b.err_temp_detect != 0
                    or error_flags.b.err_temp_disconnected != 0
                    or error_flags.b.err_eeprom != 0
                    or error_flags.b.err_flash != 0
                    or error_flags.b.err_rtc != 0
                    or error_flags.b.err_chg_mos != 0
                    or error_flags.b.err_dschg_mos != 0
                    or error_flags.b.err_prechg_mos != 0
                    or error_flags.b.err_prechg != 0
                    or error_flags.b.chg_mos_off_bus != 0
                    or error_flags.b.dschg_mos_off_bus != 0
                    or error_flags.b.chg_mos_off_switch != 0
                    or error_flags.b.dschg_mos_off_switch != 0
                    or error_flags.b.err_heating != 0
                )
                else 0
            )
            self.protection.internal_failure = 1 if (error_flags.b.err_parallel_comm != 0) else self.protection.internal_failure

            if self.daly_check_if_any_err_active(error_flags):
                self.daly_pretty_print_all_errors(error_flags)

            # error flags end#################################################################################
            self.soh = 100  # state of health info from here is inaccurate: allregsatonceB[DALY_MODBUS_ADDR_SOH-DALY_MODBUS_ADDR_CELL_TEMP_1]/10
            self.soc = allregsatonceB[DALY_MODBUS_ADDR_SOC - DALY_MODBUS_ADDR_CELL_TEMP_1] / 10
            self.temperature_1 = allregsatonceB[0] - 40
            self.temperature_2 = allregsatonceB[1] - 40
            self.temperature_mos = allregsatonceB[DALY_MODBUS_ADDR_MOS_TEMP - DALY_MODBUS_ADDR_CELL_TEMP_1] - 40

            if len(self.cells) != self.cell_count:
                self.cells = []
                for idx in range(self.cell_count):
                    self.cells.append(Cell(False))

            for i in range(self.cell_count):
                self.cells[i].voltage = cellvoltageregs[i] / 1000
                self.cells[i].balance = self.get_balancing_status_for_cellno(allregsatonceB, i)

        # if AUTO_RESET_SOC:
        #     self.update_soc_on_bms()

        return True

    def unique_identifier(self) -> str:
        """
        Used to identify a BMS when multiple BMS are connected
        """
        return self.unique_identifier_tmp

    def callback_soc_reset_to(self, path: str, value) -> bool:
        """
        Callback to reset the SOC directly on the BMS hardware (not in the driver)
        to a specific value.

        :param self: Instance of the battery class
        :param path: d-bus path of the value that changed (can be ignored in this case)
        :param value: value that was set through the GUI
        :return: True if the callback was handled successfully, False otherwise
        """
        logger.debug(f"callback_soc_reset_to called with value: {value}, setting SOC on BMS to this value")
        if value is None:
            return False

        if value < 0 or value > 100:
            return False

        self.reset_soc = value
        self.soc_to_set = value
        return self.write_soc()

    def write_soc(self):
        if self.soc_to_set is None:
            return False
        logger.debug(f"write_soc called, soc_to_set: {self.soc_to_set}")
        mbdev = mbdevs[self.address]
        time.sleep(0.2)
        with locks[self.address]:
            try:
                time.sleep(0.5)
                mbdev.write_register(DALY_MODBUS_ADDR_SET_SOC, self.soc_to_set * 10, 0, 6, False)
                self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start = (
                    self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start + 1
                )
                logger.info(
                    f"wrote {self.soc_to_set}%, soc writes since driver start: {self.Daly_HKMS_100balance_communtication_SOC_set_on_bms_since_driver_start}%"
                )
                self.soc_to_set = None  # Reset value, so we will set it only once
                return True
            except Exception as e:
                self.Daly_HKMS_100balance_communtication_error_count = self.Daly_HKMS_100balance_communtication_error_count + 1
                timesincelasterror: float = (
                    (time.time() - self.Daly_HKMS_100balance_communtication_error_last_error_time)
                    if (self.Daly_HKMS_100balance_communtication_error_last_error_time != 0)
                    else 0
                )
                self.Daly_HKMS_100balance_communtication_error_last_error_time = time.time()
                avgerrorratesincestart = (
                    time.time() - self.Daly_HKMS_100balance_communtication_start_time
                ) / self.Daly_HKMS_100balance_communtication_error_count
                logger.warning(
                    "COMM ERROR: time since last: "
                    + str(timesincelasterror)
                    + " average seconds between errors: "
                    + str(avgerrorratesincestart)
                    + " total error count: "
                    + str(self.Daly_HKMS_100balance_communtication_error_count)
                )
                logger.warning("Error setting SOC on BMS: " + str(e))
                return False
        return False

    def trigger_soc_reset(self) -> bool:
        """
        This method is called when the driver charging algorithm changes from bulk/absorption to float.
        It can be used to set the SOC on the BMS hardware (not in the driver) to 100% when the battery is
        assumed to be full

        :return: True if the callback was handled successfully, False otherwise
        """
        logger.debug("trigger_soc_reset called, setting SOC on BMS to 100%")
        self.soc_to_set = 100
        return self.write_soc()
