import asyncio
import threading

from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import time
from classes.classes import CharacterClass
from bleak.backends.characteristic import BleakGATTCharacteristic

#https://www.youtube.com/watch?v=Qb9s3UiMSTA to learn more about async in python


async def find_esp32_BLE_mac_address(esp32_name : str):
    devices = await BleakScanner.discover()
    esp32_ble_address = None
    for d in devices:
        print(d)
        if d.name == esp32_name:
            esp32_ble_address = d.address
    return esp32_ble_address

def onNotify(sender: BleakGATTCharacteristic, data: bytearray) -> None:
    print(f"Received Notification from {sender.uuid}. The following data: {data.decode()}")

class BLEManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop() #creates an unbounded event_loop object, get_event_loop() binds the event lop object to the thread of the calling code
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True) #this thread will be the thread the event loop object is bound to
        self.connection = None

    #starts the thread that will be running the event loop object.
    def start(self):
        print("starting BLE thread to connect to ESP32-S3")
        self.thread.start()
        print("Thread started ESP32-S3")

    async def establish_connection(self):
        esp32_address = await find_esp32_BLE_mac_address("Guiness")
        if esp32_address is None:
            print("Unable to detect ESP32 device.")
            return
        # bleak_client = BleakClient(esp32_address, winrt={"use_cached_services": False})
        bleak_client = BleakClient(esp32_address)
        await bleak_client.connect()
        if not bleak_client.is_connected:
            print("Unable to connect to ESP32")
            return



        # --- hello: dump every service + characteristic the client discovered ---
        # bleak 3.0.2: client.services is a BleakGATTServiceCollection (iterable -> services);
        # each service.characteristics is a list[BleakGATTCharacteristic]. This shows the
        # GATT table AS bleak sees it -- which on Windows may be a *cached* table (see below).
        print(f"=== GATT table discovered on {esp32_address} ===")
        for service in bleak_client.services:
            print(f"[service] {service.uuid}  handle={service.handle}  ({service.description})")
            for char in service.characteristics:
                print(f"    [char] {char.uuid}  handle={char.handle}  props={char.properties}  ({char.description})")
        print("=== end GATT table ===")
        # --- end diagnostic ---

        characteristic = bleak_client.services.get_characteristic("61adcd67-20e9-4c38-b1d0-879445c5b732")
        if characteristic is None:
            print("Failed to locate characteristic on ESP32")
            return
        # Diagnostic: write_gatt_char with response=True performs a write-WITH-response
        # (the characteristic is declared READ | WRITE), so it waits for the device's
        # ATT acknowledgment and raises on failure — same UNREACHABLE family as the read
        # path if the link drops between discovery and the write (see ISSUES.md #2/#3).
        # response=True is passed explicitly because bleak's response=None default is
        # deprecated. data must support the buffer protocol (bytes here).
        # time.sleep(10)
        # print("is is still connected:", bleak_client.is_connected)
        print("connected before write:ohellohellohellohello", bleak_client.is_connected)
        await bleak_client.start_notify("89f7c2ca-89b5-4234-905c-d4a931feaa3b", onNotify)

        payload = "a".encode("ascii")
        for attempt in range(3):
            try:
                print(f"wrote {payload} to characteristic")
                await bleak_client.write_gatt_char(characteristic, payload)
                break
            except BleakError as e:
                print(f"write attempt {attempt} failed (connected={bleak_client.is_connected}): {e}")
                await asyncio.sleep(1)

        print("Now waiting for notification to see if it comes back")
        await asyncio.sleep(10) ##Give time for notification to come back before




    # async def disconnect_connection(self):
    # #run this first to get the appropriate MAC address of your BLE device. Establish c
    # # onnection later using that MAC address in your main.

    #expects an async function to pass to the event loop.
    def pass_coroutine_to_event_loop_and_block_till_future_resolves(self, coroutine):
         future = asyncio.run_coroutine_threadsafe(coroutine, self.loop) #returns a Concurrent.Future.future object (threadsafe), not asyncio.future (not threadsafe). run_coroutine_threadsafe() acts as a bridge as it allows async code to be called from a synchronous context (and implicitly across threads since asnc code and sync code cannot be in the same thread), and we can mimic the functionality of await in that synchronous context through the use of .result()
         result = future.result() #.result() is a blocking operation that only unblocks the thread calling this function once the future returned by the event loop running in the background thread has a value.
         return result


bleManagerTest = BLEManager()
bleManagerTest.start()
# print("ESP32 Named Guiness found. It has address:", bleManagerTest.pass_coroutine_to_event_loop_and_block_till_future_resolves(find_esp32_BLE_mac_address("Guiness")))
bleManagerTest.pass_coroutine_to_event_loop_and_block_till_future_resolves(bleManagerTest.establish_connection())






