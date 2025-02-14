#!/usr/bin/env python3
import smach
import rospy
from geometry_msgs.msg import Pose, Point, Quaternion

class GoToWaitLocation(smach.State):
    def __init__(self, context):
        smach.State.__init__(self, outcomes=['done', 'not done'])
        self.context = context

    def execute(self, userdata):
        wait_location = rospy.get_param("/wait")
        position, orientation = wait_location["location"]["position"], wait_location["location"]["orientation"]
        done = not len([(label, table) for label, table in self.context.tables.items() if table["status"] == "ready"])
        if not done:
            self.context.voice_controller.sync_tts("I am going to wait for a new customer")
        self.context.base_controller.sync_to_pose(Pose(position=Point(**position), orientation=Quaternion(**orientation)))
        return 'done' if done else 'not done'
