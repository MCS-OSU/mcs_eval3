from dataclasses import dataclass
from gravity import pybullet_utilities
import numpy as np
DEBUG = False

@dataclass
class ObjectFace:
    corners: list

    def __post_init__(self):
        x = sum(pt["x"] for pt in self.corners) / 4
        y = sum(pt["y"] for pt in self.corners) / 4
        self.centroid = (x, y)

    def __eq__(self, o: object, tol=1e-5) -> bool:
        # Assuming Object is rigid & won't deform during the motion
        x_matches = abs(self.centroid[0] - o.centroid[0]) < tol
        y_matches = abs(self.centroid[1] - o.centroid[1]) < tol

        return x_matches and y_matches

class GravityAgent:
    def __init__(self, controller, level):
        self.controller = controller
        self.level = level

    @staticmethod
    def determine_drop_step(pole_state_history):
        '''
        Find the precise point when the suction is off.
        '''
        # Based on an assumption that the pole color changes after the drop (suction off)

        pole_color_history = [md["texture_color_list"][0] for md in pole_state_history]
        init_color = pole_color_history[0]

        for idx, color in enumerate(pole_color_history):
            if color != init_color:
                return idx - 1
        # else:
        #     raise(Exception("Drop step not detected by observing color of pole"))
        return -1
    @staticmethod
    def get_object_bounding_simplices(dims):
        y_coords = [pt["y"] for pt in dims]
        min_y, max_y = min(y_coords), max(y_coords)

        # Assuming "nice" placement with cubes
        # For generalizing, calculate convex hull and findout extreme simplices 
        bottom_face = ObjectFace(corners=[pt for pt in dims if pt["y"] == min_y ])
        top_face = ObjectFace(corners=[pt for pt in dims if pt["y"] == max_y ])

        return top_face, bottom_face

    def states_during_and_after_drop(self, drop_step, target_trajectory, support):

        # Assuming target is moving along "y"
        target_drop_coords = target_trajectory[drop_step]
        _, target_bottom_face = self.get_object_bounding_simplices(target_drop_coords)
        
        target_resting_coords = target_trajectory[-1]
        _, target_bottom_face_end_state = self.get_object_bounding_simplices(target_resting_coords)

        support_top_face, _ = self.get_object_bounding_simplices(support)

        return target_bottom_face, support_top_face, target_bottom_face_end_state


    @staticmethod
    def target_should_be_stable(target, support):

        support_x_range = (min(pt["x"] for pt in support.corners), max(pt["x"] for pt in support.corners))
        support_y_range = (min(pt["y"] for pt in support.corners), max(pt["x"] for pt in support.corners))

        target_x_inrange = support_x_range[0] <= target.centroid[0] <= support_x_range[1]
        target_y_inrange = support_y_range[0] <= target.centroid[1] <= support_y_range[1]

        return target_x_inrange and target_y_inrange

    @staticmethod
    def target_obj_id(step_output):
        if len(list(step_output['object_list'].keys())) > 0:
            return list(step_output['object_list'].keys()).pop()
        return None

    @staticmethod
    def struc_obj_ids(step_output):
        filtered_keys = [
            key 
            for key in step_output["structural_object_list"].keys() 
            if len(key) > 35
        ]
        out = dict({
            ("pole", so) if "pole_" in so else ("support", so)
            for so in filtered_keys
        }
        )
        return out.get("support"), out.get("pole")

    def sense_voe(self, drop_step, support_coords, target_trajectory):
        '''
        Assumptions:
        -> Objects are assumed to be rigid with uniform mass density
        -> Supporting object is assumed to be at rest
        -> Law of conservation of energy is ignored
        -> Accn. due to gravity & target object velocity are along the "y" direction
        '''

        # Surface states when the target is (possibly) placed on support
        target, support, target_end = self.states_during_and_after_drop(
            drop_step, target_trajectory, support_coords
        )
        # Determine if target should rest
        target_should_rest = self.target_should_be_stable(target, support)

        # Now verify if the target's final state is consistent with the above
        target_actually_rested = target == target_end

        return target_should_rest ^ target_actually_rested

    def run_scene(self, config, desc_name):
        if DEBUG:
            print("DEBUG MODE!")
        self.controller.start_scene(config)

        # Inputs to determine VoE
        target_trajectory = []
        pole_states = []  # To determine drop step
        support_coords = None

        obj_traj_orn = None
        previous_step = None
        step_output = None
        for i, x in enumerate(config['goal']['action_list']):
            previous_step = step_output
            step_output = self.controller.step(action=x[0])

            if step_output is None:
                break
            else:
                step_output = dict(step_output)

            floor_object = "floor"  # Not a dynamic object ID
            target_object = self.target_obj_id(step_output)
            supporting_object, pole_object = self.struc_obj_ids(step_output)
            # Collect observations
            # if support_coords is None:
            #     support_coords = step_output["object_list"]["supporting_object"]["dimensions"]

            try:
                # Expected to not dissapear until episode ends
                support_coords = step_output["structural_object_list"][supporting_object]["dimensions"]
                floor_coords = step_output["structural_object_list"][floor_object]["dimensions"]
                # Target / Pole may not appear in view yet, start recording when available
                target_trajectory.append(step_output["object_list"][target_object]["dimensions"])
                pole_states.append(step_output["structural_object_list"][pole_object])
            except KeyError:  # Object / Pole is not in view yet
                pass

            if len(pole_states) > 1 and self.determine_drop_step(pole_states) == len(pole_states) - 2:
                obj_traj_orn = pybullet_utilities.render_in_pybullet(step_output, target_object, supporting_object, self.level)
            
            choice = plausible_str(True)
            voe_xy_list = []
            voe_heatmap = None
            self.controller.make_step_prediction(
                choice=choice, confidence=1.0, violations_xy_list=voe_xy_list,
                heatmap_img=voe_heatmap)

        if obj_traj_orn != None:
            # simulator trajectory
            sim_start_pos = np.array(obj_traj_orn[target_object]['pos'][0])
            sim_end_pos = np.array(obj_traj_orn[target_object]['pos'][-1])
            print("target start/end pos", sim_start_pos, sim_end_pos)
            #final step out
            unity_end_pos = list(step_output["object_list"][target_object]["position"].values())
            unity_end_pos = np.array([unity_end_pos[0], unity_end_pos[2], unity_end_pos[1]])
            end_pos_diff = np.linalg.norm(unity_end_pos - sim_end_pos)
            if end_pos_diff >= 0.25:
                print("Physics Sim Suggests VoE!")

        drop_step = self.determine_drop_step(pole_states)
        voe_flag = self.sense_voe(drop_step, support_coords, target_trajectory)

        if voe_flag:
            print("VoE!")
        else:
            print("No VoE.")

        self.controller.end_scene(choice=plausible_str(voe_flag), confidence=1.0)
        return True

    def calc_voe(self, step_output, frame_num, scene_name=None):
        pass

def plausible_str(violation_detected):
    return 'implausible' if violation_detected else 'plausible'