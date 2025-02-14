import rospy
import rosparam
from tiago_controllers import BaseController, HeadController
from lasr_voice import Voice
from play_motion_msgs.msg import PlayMotionAction
from control_msgs.msg import PointHeadAction
from lasr_object_detection_yolo.srv import YoloDetection
from coffee_shop.srv import TfTransform, LatestTransform, ApplyTransform
from lasr_shapely import LasrShapely
from lasr_speech.srv import Speech
from cv_bridge3 import CvBridge
import actionlib
import yaml
from visualization_msgs.msg import Marker
import rosservice
import time
import random

class Context:

    def __init__(self, config_path=None):
        self.base_controller = BaseController()
        rospy.loginfo("Got base controller")
        self.head_controller = HeadController()
        rospy.loginfo("Got head controller")
        self.voice_controller = Voice()
        rospy.loginfo("Got voice controller")
        self.play_motion_client = actionlib.SimpleActionClient('/play_motion', PlayMotionAction)
        self.play_motion_client.wait_for_server()
        rospy.loginfo("Got PM")
        self.point_head_client = actionlib.SimpleActionClient('/head_controller/point_head_action', PointHeadAction)
        self.point_head_client.wait_for_server()
        rospy.loginfo("Got PH")
        rospy.wait_for_service('/yolov8/detect')
        self.yolo = rospy.ServiceProxy('/yolov8/detect', YoloDetection)
        rospy.loginfo("Got YOLO")
        rospy.wait_for_service("/tf_transform")
        rospy.wait_for_service("/get_latest_transform")
        rospy.wait_for_service("/apply_transform")
        self.tf = rospy.ServiceProxy("/tf_transform", TfTransform)
        self.tf_latest = rospy.ServiceProxy("/get_latest_transform", LatestTransform)
        self.tf_apply = rospy.ServiceProxy("/apply_transform", ApplyTransform)
        rospy.loginfo("Got TF")
        self.shapely = LasrShapely()
        rospy.loginfo("Got shapely")
        self.bridge = CvBridge()
        rospy.loginfo("CV Bridge")
        rospy.wait_for_service("/lasr_speech/transcribe_and_parse")
        self.speech = rospy.ServiceProxy("/lasr_speech/transcribe_and_parse", Speech)
        rospy.loginfo("Speech")

        if '/pal_startup_control/start' in rosservice.get_service_list():
            # Assume that if the topics are available, then the services are running.
            try:
                from pal_startup_msgs.srv import StartupStart, StartupStop
                self.start_head_manager = rospy.ServiceProxy("/pal_startup_control/start", StartupStart)
                self.stop_head_manager = rospy.ServiceProxy("/pal_startup_control/stop", StartupStop)
            except ModuleNotFoundError:
                self.start_head_manager = lambda a, b : None
                self.stop_head_manager = lambda a : None   
        else:
            self.start_head_manager = lambda a, b : None
            self.stop_head_manager = lambda a : None


        self.tables = dict()
        self.target_object_remappings = dict()

        if config_path is not None:
            with open(config_path, "r") as fp:
                data = yaml.safe_load(fp)

            self.tables = {
                table: {"status" : "unvisited", "people": list(), "order": list()} for table in data.get("tables", dict()).keys()
            }

            self.target_object_remappings = data.get("objects", dict())

            self.YOLO_person_model = data.get("yolo_person_model", "yolov8n-seg.pt")
            self.YOLO_objects_model = data.get("yolo_objects_model", "yolov8n-seg.pt")

        else:
            rospy.logerr("No config_path was given.")

        self.current_table = None
        self.new_customer_pose = None

        self._people_pose_pub = rospy.Publisher("/people_poses", Marker, queue_size=100)
        self._people_idx = 0
        self._object_pose_pub = rospy.Publisher("/object_poses", Marker, queue_size=100)
        self._objects_idx = 0

        self.retry_utterances = [
            "Sorry, I didn't get that. Could you repeat, please?",
            "I didn't quite get that. Please repeat!",
            "Can you say that again, please?",
            "I think my ears need cleaning, could you say that again?",
            "Please could you repeat that",
            "Can you repeat yourself, and possibly speak louder, please?"
        ]

    @staticmethod
    def _create_point_marker(idx, x, y, z, frame_id, r, g, b):
        marker_msg = Marker()
        marker_msg.header.frame_id = frame_id
        marker_msg.header.stamp = rospy.Time.now()
        marker_msg.id = idx
        marker_msg.type = Marker.SPHERE
        marker_msg.action = Marker.ADD
        marker_msg.pose.position.x = x
        marker_msg.pose.position.y = y
        marker_msg.pose.position.z = z
        marker_msg.pose.orientation.w = 1.0
        marker_msg.scale.x = 0.1
        marker_msg.scale.y = 0.1
        marker_msg.scale.z = 0.1
        marker_msg.color.a = 1.0
        marker_msg.color.r = r
        marker_msg.color.g = g
        marker_msg.color.b = b
        return marker_msg

    def publish_person_pose(self, x, y, z, frame_id):
        self._people_pose_pub.publish(self._create_point_marker(self._people_idx, x, y, z, frame_id, 1.0, 0.0, 0.0))
        self._people_idx += 1

    def publish_object_pose(self, x, y, z, frame_id):
        self._object_pose_pub.publish(self._create_point_marker(self._objects_idx, x, y, z, frame_id, 0.0, 1.0, 0.0))
        self._objects_idx += 1

    def __str__(self):
        table_summary = []
        for table, table_info in self.tables.items():
            status = table_info["status"]
            people = table_info["people"]
            order = table_info["order"]
            table_summary.append(f"Table {table}: Status={status}, People={people}, Order={order}")

        return "\n".join(table_summary)

    def get_random_retry_utterance(self):
        # Seed the random number generator with the current time
        random.seed(time.time())
        return random.choice(self.retry_utterances)

    def get_interaction_person(self):
        # Seed the random number generator with the current time
        random.seed(time.time())
        return random.choice(self.tables[self.current_table]["people"])
