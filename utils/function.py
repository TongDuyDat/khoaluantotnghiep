import os
import sys 
WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(WORKING_DIR, "../"))
import cv2
import json
import time
import requests
import traceback
from config import settings
from threading import Thread
from datetime import datetime
from shapely.geometry import Polygon
from detect import get_detected_object_v8
from urllib.request import urlopen as url
from utils.plots import draw_object_bboxes, draw_warning_area, convert_name_id

class WebcamVideoStream:
    def __init__(self, src=0, name="WebcamVideoStream"):
		# initialize the video camera stream and read the first frame
        self.src = src
		# from the stream
        self.stream = cv2.VideoCapture(src)
        (self.grabbed, self.frame) = self.stream.read()

		# initialize the thread name
        self.name = name

		# initialize the variable used to indicate if the thread should
		# be stopped
        self.stopped = False

    def open(self):
        return self.stream
    
    def start(self):
		# start the thread to read frames from the video stream
        t = Thread(target=self.update, name=self.name, args=())
        t.daemon = True
        t.start()
        return self

    def update(self):
        # keep looping infinitely until the thread is stopped
        while True:
            # if the thread indicator variable is set, stop the thread
            if self.stopped:
                return

            # otherwise, read the next frame from the stream
            (self.grabbed, self.frame) = self.stream.read()

    def read(self):
		# return the frame most recently read
        return self.grabbed, self.frame

    def stop(self):
		# indicate that the thread should be stopped
        self.stopped = True

    def release(self):
		# return the frame most recently read
        self.stream.release()

class VideoStream:
    def __init__(self, src=0):
		# otherwise, we are using OpenCV so initialize the webcam
		# stream
        self.stream = WebcamVideoStream(src=src)

    def start(self):
		# start the threaded video stream
        return self.stream.start()
    
    def open(self):
        return self.stream.open()

    def update(self):
		# grab the next frame from the stream
        self.stream.update()

    def read(self):
		# return the current frame
        return self.stream.read()

    def stop(self):
		# stop the thread and release any resources
        self.stream.stop()

    def release(self):
        self.stream.release()


def reset_attempts():
    return 50

def process_video(attempts, camera):
    while(True):
        (grabbed, frame) = camera.read()
        if not grabbed:
            print("[INFO] Disconnected!")
            camera.release()

            if attempts > 0:
                time.sleep(5)
                return True
            else:
                return False
        else:
            '''Read the camera resize frame'''
            frame_resize_output = cv2.resize(frame, (853, 480))
            (flag, encodedImage) = cv2.imencode(".jpg", frame_resize_output)
            # ensure the frame was successfully encoded
            if not flag:
                continue
            # yield the output frame in the byte format
            yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + 
                bytearray(encodedImage) + b'\r\n')


def connect_camera(URL):
    recall = True
    attempts = reset_attempts()

    while(recall):
        camera = VideoStream(URL).start()

        if camera.open().isOpened():
            print("[INFO] Camera connected at " +
                datetime.now().strftime("%m-%d-%Y %I:%M:%S%p"))
            attempts = reset_attempts()
            recall = process_video(attempts, camera)
            return recall

        else:
            print("[INFO] Camera not opened " +
                datetime.now().strftime("%m-%d-%Y %I:%M:%S%p"))
            camera.release()
            attempts -= 1
            print("[INFO] Attempts: " + str(attempts))

            # give the camera some time to recover
            for i in range(5):
                print(f'Time: {i+1}s')
                time.sleep(1)

            continue


'''Send notifications when unusual object was detected'''
def post_notification(data_send, ip_camera, messages):
    try:
        result = ', '.join(messages)
        url = f"{settings.URLSV}/warning"
        payload={'content': result,
        'object': data_send['objects'],
        'camera_ip': ip_camera,
        'confirm_status': 'CHUA_XAC_NHAN'}
        files=[
            ('file',(data_send['img_name'],open(data_send['detected_image_path'],'rb'),'image/jpeg'))
        ]
        headers = {}

        response = requests.request("POST", url, headers=headers, data=payload, files=files)
        print("[INFO] Notifications sent successfully!")
    except:
        print("[INFO] Notifications sent fail!")
        print('[INFO] Error:')
        traceback.print_exc() 
        pass


'''Send health status of edge com to server'''
def health_check_nano(ip_edgecom):
    try:
        url = f"{settings.URLSV}/jetson/status/{ip_edgecom}"
        payload="{\r\n    \"status\": true\r\n}"
        headers = {
        'Content-Type': 'application/json'
        }
        response = requests.request("PUT", url, headers=headers, data=payload)
        print("[INFO] Health check sent successfully!")
    except:
        print("[INFO] Health check sent fail!")
        print('[INFO] Error:')
        traceback.print_exc() 
        pass

def overlap(bbox_a, bbox_b):
    
    xmin1, ymin1, xmax1, ymax1 = bbox_a["xmin"], bbox_a['ymin'], bbox_a["xmax"], bbox_a['ymax']
    xmin2, ymin2, xmax2, ymax2 = bbox_b["xmin"], bbox_b['ymin'], bbox_b["xmax"], bbox_b['ymax']

    # Calculate the coordinates of the intersection rectangle
    x_left = max(xmin1, xmin2)
    y_top = max(ymin1, ymin2)
    x_right = min(xmax1, xmax2)
    y_bottom = min(ymax1, ymax2)

    if x_right < x_left or y_bottom < y_top:
        return 0.0  # No overlap

    # Calculate the area of intersection rectangle
    intersection_area = (x_right - x_left) * (y_bottom - y_top)

    # Calculate the area of both rectangles
    bbox1_area = (xmax1 - xmin1) * (ymax1 - ymin1)
    bbox2_area = (xmax2 - xmin2) * (ymax2 - ymin2)

    # Calculate the overlap ratio
    overlap_ratio = intersection_area / float(bbox1_area + bbox2_area - intersection_area)

    return overlap_ratio

def double_check_class(classified_t, classified_prev):
    box_t, box_prev = [], []
    classified_suspects = []
    for cls_t in classified_t:
        print(cls_t)
        if float(cls_t["score"]) > 0.8:
            box_t.append(cls_t)
        else:
            classified_suspects.append(cls_t)
    
    for cls_suspect in classified_suspects:
        count = 0
        for cls_prev in classified_prev:
            if cls_suspect['label'] != cls_prev['label']:
                continue
            if overlap(cls_suspect, cls_prev) > 0.7:
                box_t.append(cls_suspect)
            else:
                count+=1
        if count == len(classified_prev):
            box_prev.append(cls_suspect)
            
    if len(classified_prev) == 0:
        box_prev = classified_suspects
    return box_t, box_prev
    
    
        
classified_prev = []

def detect_v8(image, ip_camera, pts, conf_thres, iou_thres, model, json_object, use_tele, camera_name):
    global classified_prev
    try:
        input_image = f'{settings.IMAGE_FOLDER}/original.jpg' # original image path
        cv2.imwrite(input_image, image) # save original image
        classified = get_detected_object_v8(image, conf_thres, iou_thres, model, json_object) # objects detection on image with yolov8            
        if len(classified) != 0:
            classified_overlap = check_overlap(classified, pts)  
            if len(classified_overlap) != 0:
                #check ở đây
                classified_overlap, classified_suspects = double_check_class(classified_overlap, classified_prev)
                classified_prev = classified_suspects
                #visualization
                im_draw_warning_area = draw_warning_area(image, pts) # image drawing warning area
                im_show = draw_object_bboxes(im_draw_warning_area, classified_overlap, json_object) # image drawing object bboxes
                cv2.imwrite(f'{settings.IMAGE_FOLDER}/detected.jpg', im_show)
                
                # get infomation
                status, messages = get_message(classified_overlap, json_object)
                print(f'[INFO] {messages}')
                try:
                    # print("Thông báo")
                    post_notification(status, ip_camera, messages) # send notification to server
                except UnboundLocalError:
                    pass
        if use_tele == 1:
            print('[INFO] Sending photos to telegram ...')
            id = "4007916448"
            token = "7048578078:AAE_wZq-i1wgXYfaQzeU7unGIDaToCbZuv4"
            send_telegram(input_image, token, id, camera_name)     
        else:
            print('[INFO] Good!')
    except:
        print('[INFO] Detected object fail.')
        print('[INFO] Error:')
        traceback.print_exc()
        pass


'''Update information from server into json file'''
def get_information_from_server(IPCAM, IPEDCOM):
    try:
        url = f"{settings.URLSV}/camera/getCameraByIp/{IPCAM}"

        payload="{\r\n    \"jetson_ip_address\":\"" + f"{IPEDCOM}" "\"\r\n}"
        headers = {
        'Content-Type': 'application/json'
        }

        response = requests.request("GET", url, headers=headers, data=payload)

        info = response.text
        res = json.loads(info)
        for information in res['data']:
            camera_name = information['name']
            time_detect = information['identification_time']
            brand_name = information['type_id']['brand']
            api_name = information['api_name']
            coor = information['detect_point']

        json_file = open(os.path.join(os.getcwd(), 'info.json'), "r")
        data = json.load(json_file)
        json_file.close()
        data['name_camera'] = camera_name     
        data['identification_time'] = time_detect
        data['api_name'] = api_name
        data['type_camera'] = brand_name
        data['coordinate'] = coor

        # Save our changes to JSON file
        json_file = open(os.path.join(os.getcwd(), 'info.json'), "w+")
        json_file.write(json.dumps(data, indent = 5))
        json_file.close()
        print('[INFO] Updated information from server successfully.')

    except:
        print("[INFO] Updated information from server fail.")
        print('[INFO] Error:')
        traceback.print_exc()   


'''Write H and W to json file'''
def update_frame_dimension(HEIGHTCAM, WIDTHCAM, IPCAM):
    try:
        url = f"{settings.URLSV}/camera/updateSizeCamera/{IPCAM}"

        payload = json.dumps({
        "cam_width": WIDTHCAM,
        "cam_height": HEIGHTCAM
        })
        headers = {
        'Content-Type': 'application/json'
        }

        response = requests.request("PUT", url, headers=headers, data=payload)        
        print('[INFO] Updated H and W to server successfully.')
    except:
        print("[INFO] Updated H and W to server fail.")
        print('[INFO] Error:')
        traceback.print_exc()     


'''Ping to check internet'''
def checking_internet():
    status = ''
    count_seconds = 0
    print("[INFO] Checking internet at " +
        datetime.now().strftime("%m-%d-%Y %I:%M:%S%p"))    
    while(True):
        try:
            url('https://google.com.vn/', timeout=3) # UBUNTU
            # os.system('ping 1.1.1.1') # WIN
            status = True
        except Exception as e:
            status = False
        
        if status == True:
            print('[INFO] Internet is available.')
            break
        else:
            print('[INFO] Internet is not available.')
            print(f'[INFO] Time left to reboot if there is no internet connection: {180 - count_seconds}s.')
            for i in range(5):
                print(f'Time: {i+1}s')
                time.sleep(1)
                count_seconds += 1

            if count_seconds == 180:
                os.system("sudo reboot")
            
            continue


def checking_internet_auto():
    status = ''
    count_seconds = 0
    while(True):
        print("[INFO] Checking internet auto at " +
            datetime.now().strftime("%m-%d-%Y %I:%M:%S%p"))
        try:
            url('https://google.com.vn/', timeout=3) # UBUNTU
            status = True
        except Exception as e:
            status = False
        
        if status == True:
            print('[INFO] Internet is available.')
            time.sleep(180)
            continue
        else:
            print('[INFO] Internet is not available.')
            print(f'[INFO] Time left to reboot: {180 - count_seconds}s.')
            for i in range(5):
                print(f'Time: {i+1}s')
                time.sleep(1)
                count_seconds += 1
            if count_seconds == 180:
                print("[INFO] Open Sim 4G network again ...")
                cmd_enable_sim = 'sudo ifmetric wwan0 50'
                try:
                    os.system(cmd_enable_sim)
                    print("[INFO] Done!!")
                except Exception as e:
                    print("[INFO] Sim not found!!")
                    pass                                
            elif count_seconds == 260:
                print('[INFO] Reboot now ...')
                os.system('sudo reboot')
            
            continue
        

'''Ping to check camera'''
def checking_camera(URL):
    while(True):
        cap = VideoStream(URL).start()
        grabbed, frame = cap.read()
        if grabbed:
            print('[INFO] Connected camera successfully.')
            cap.stop()
            break
        else:
            print('[INFO] Connected camera fail, again ...')
            for i in range(5):
                print(f'Time: {i+1}s')
                time.sleep(1)
            continue

    cap.stop()

    return grabbed, frame


'''Check if bbox of object touch to warning area'''
def check_overlap(classified, PTS_Area):
    new_classified = []
    if len(PTS_Area) != 0:
        if len(PTS_Area) == 2:
            xmin = PTS_Area[0][0]
            xmax = PTS_Area[1][0]
            ymin = PTS_Area[0][1]
            ymax = PTS_Area[1][1]
            PTS_Area = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]

        for info in classified:
            xmin = info['xmin']
            ymin = info['ymin']
            xmax = info['xmax']
            ymax = info['ymax']
            PTS_Object = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
            polygon1 = Polygon(PTS_Area)
            polygon2 = Polygon(PTS_Object)
            intersect = polygon1.intersection(polygon2).area
            union = polygon1.union(polygon2).area
            iou = intersect / union
            if iou != 0:
                new_classified.append(info)
    else:
        new_classified = classified

    return new_classified


'''Convert to the correct message format to send to the server'''
def get_message(classified, json_object):
    messages = []
    result = []
    # get infomation
    # initialize an empty dictionary to store label counts
    label_counts = {}

    # iterate over each dictionary in the list
    for item in classified:
        label = item['label']
        label_counts[label] = label_counts.get(label, 0) + 1 # increment the count for the label

    for label, count in label_counts.items():
        vn_label = convert_name_id(label, 'vietnamese_name', json_object)
        s = f"Phát hiện {count} {vn_label}"
        messages.append(s)                
        info_label = {
                "label": label.upper(),
                "numbers": count
            }
        result.append(info_label)

    status = {
        'total_objects' : len(classified),
        'objects': str(result),
        'img_name' : 'detected.jpg',
        'detected_image_path': f'{settings.IMAGE_FOLDER}/detected.jpg'
    }

    return status, messages


def analyze():
    from jtop import jtop
    while(True):
        with jtop() as jetson:
            # jetson.ok() will provide the proper update frequency
            while jetson.ok():
                # Read tegra stats
                stats = jetson.stats
                print("[INFO] Analyze system at " +
                    datetime.now().strftime("%m-%d-%Y %I:%M:%S%p"))     
                Temp_AO = stats['Temp AO']
                Temp_CPU = stats['Temp CPU']
                Temp_GPU = stats['Temp GPU']
                Temp_PLL = stats['Temp PLL']
                print(f'[INFO] Temperature\nAO: {Temp_AO}\nCPU: {Temp_CPU}\nGPU: {Temp_GPU}\nPLL: {Temp_PLL}\n')
                time.sleep(60)


def send_telegram(path, token, id, camera_name):
    caption = f'[INFO] {camera_name} ' + datetime.now().strftime("%m-%d-%Y %I:%M:%S%p")
    try:
        files = {'photo':open(path,'rb')}
        resp = requests.post(f'https://api.telegram.org/bot{token}/sendPhoto?chat_id=-{id}&caption={caption}', files=files)
        print('[INFO] Sent to telegram successfully at ' + datetime.now().strftime("%m-%d-%Y %I:%M:%S%p") + '.')
    except UnboundLocalError as e:
        print('[INFO] Sent to telegram fail ...')
        print('[INFO] Error:')
        print(e)
        traceback.print_exc() 
        pass