//This works as expected but my own implementation doesnt.
// #include <NimBleKeyboard.h>
// BleKeyboard bleKeyboard;

// void setup() {
//   Serial.begin(115200);
//   Serial.println("Starting NimBLE work!");
//   bleKeyboard.begin();
// }

// void loop() {
//   if(bleKeyboard.isConnected()) {
//     Serial.println("Sending 'Hello world'...");
//     bleKeyboard.print("Hello world");

//     delay(1000);

//     Serial.println("Sending Enter key...");
//     bleKeyboard.write(KEY_RETURN);

//     delay(1000);

//     Serial.println("Sending Play/Pause media key...");
//     bleKeyboard.write(KEY_MEDIA_PLAY_PAUSE);

//     delay(1000);
    
//    //
//    // Below is an example of pressing multiple keyboard modifiers 
//    // which by default is commented out. 
//    // 
//    /* Serial.println("Sending Ctrl+Alt+Delete...");
//     bleKeyboard.press(KEY_LEFT_CTRL);
//     bleKeyboard.press(KEY_LEFT_ALT);
//     bleKeyboard.press(KEY_DELETE);
//     delay(100);
//     bleKeyboard.releaseAll();
//     */

//   }
//   Serial.println("Waiting 5 seconds...");
//   delay(5000);
// }


#include <NimBleKeyboard.h>

#define SERVICE_UUID        "a723b49a-2b53-46a6-a2db-02bb9e9a577c"
#define KEYBOARDINPUTCHARACTERISTIC_UUID "61adcd67-20e9-4c38-b1d0-879445c5b732"
#define PCRESPONSECHARACTERISTIC_UUID "89f7c2ca-89b5-4234-905c-d4a931feaa3b"

class CommandCallbacks : public NimBLECharacteristicCallbacks {
  NimBLECharacteristic* pPCResponseCharacteristic;
  BleKeyboard* HelixBLEKeyboard;


  public:
  CommandCallbacks(NimBLECharacteristic* pPCResponseCharacteristic, BleKeyboard* HelixBLEKeyboard) : pPCResponseCharacteristic(pPCResponseCharacteristic), HelixBLEKeyboard(HelixBLEKeyboard) {} //uses intialiser list

  void onWrite(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo) override {
    Serial.println("OnWrite is firing");
    NimBLEAttValue val = pChar->getValue();   // the bytes just written
    String val_str = String(val.c_str());
    Serial.println((uint64_t) HelixBLEKeyboard);
    // char c = val[0];
    Serial.println("Received:" + val_str);
    // Convert NimBLEAttValue to an Arduino String and concatenate
    String responseMsg = "Latest written value into pKeyboardInputCharacteristic:" + val_str; 
    HelixBLEKeyboard->print(val_str);
    // pPCResponseCharacteristic->setValue(responseMsg);
    // pPCResponseCharacteristic->notify(); 
    }
};

class HelixBLEKeyboard : public BleKeyboard {
  NimBLEService* pService;
  NimBLECharacteristic* pCharacteristic1;
  NimBLECharacteristic* pCharacteristic2;


  public:
  virtual void onStarted(NimBLEServer *pServer) override {
    pService = pServer->createService(SERVICE_UUID);
    pCharacteristic1 = pService->createCharacteristic(KEYBOARDINPUTCHARACTERISTIC_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE);
    pCharacteristic2 = pService->createCharacteristic(PCRESPONSECHARACTERISTIC_UUID, NIMBLE_PROPERTY::NOTIFY); //TODO hook this up on the PC side, need to get PC to subscrie to this. For now i'll impleent everything else on this side but leave the PC side for later
    CommandCallbacks* customNotifyCallbackObject = new CommandCallbacks(pCharacteristic2, this);
    pCharacteristic1->setCallbacks(customNotifyCallbackObject);
   };

   //fires when windows connects, does not fire when we connect through python script but device still advertises, maybe stop it form advertising? Though i dont think this is the issue to our current problem of onwrite not writing back to the PC.
   virtual void onConnect(NimBLEServer* pServer, NimBLEConnInfo &connInfo) override{
    Serial.println("OnConnect method executed, advertising again.");
    BleKeyboard::onConnect(pServer, connInfo);
    pServer->getAdvertising()->start();
    }
};


int count = 0;

HelixBLEKeyboard bleKeyboard; 
// BleKeyboard bleKeyboard;

void setup() {
  Serial.begin(115200);
  Serial.println("Starting NimBLE work!");
  bleKeyboard.setName("Guiness");
  bleKeyboard.begin();
}

void loop() {
  // Serial.println("is the BLEKeyboard connected on the esp32 side:" + String(bleKeyboard.isConnected()));
  if (count == 0){
    count += 1;
    unsigned long int x = (uint64_t) &bleKeyboard;
    Serial.println(x);
  }
  // if (bleKeyboard.isConnected()) {
  // count += 1;
  // Serial.println("ESP32 connected as bluetooth keyboard:" + String(bleKeyboard.isConnected()));
  // Serial.println("Run python script now to connect to ESP32 to begin sending commands");
  // String x = String("hellofriend");
  // bleKeyboard.print(x);
  // Serial.println("keyboard should've written: hello");
  // Serial.println("Waiting 5 seconds...");
  // delay(5000);
  // };
}