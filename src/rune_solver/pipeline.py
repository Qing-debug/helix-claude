from ultralytics import YOLO


#change this ot use the onnx runtime instead since we plan to make deployment platform agnostic + models should run on CPU
A = YOLO(r".../modeltraining/models/stage_a_panel.pt")   # names {0: 'Rune_Panel'}
B = YOLO(r".../modeltraining/models/stage_b_arrows.pt")  # names {0:'up',1:'down',2:'left',3:'right',4:'spinning'}

#stage B requires extra post processing (the geometric thing we discussed so we can implement all that in the wrapper class, and both stage A and B require that IOU functionality to be added)

#for each image that contains a spinning rune, either run it through stage A if its a full frame then stage B, or just stage B if its its the appropriate format stage B expects.
