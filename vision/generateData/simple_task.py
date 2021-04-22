from MCS_exploration.gym_ai2thor.envs.mcs_env import McsEnv
from MCS_exploration.meta_controller.meta_controller import MetaController
from MCS_exploration.frame_collector import Frame_collector
import sys
import configparser

DEBUG = False



if __name__ == "__main__":
    start_scene_number = 0

    config_ini = configparser.ConfigParser()
    config_ini.read("mcs_config.ini")
    level = config_ini['MCS']['metadata']

    collector = Frame_collector(scene_dir="simple_task_img", start_scene_number=start_scene_number)
    env = McsEnv(
        task="interaction_scenes", scene_type="retrieval" if not DEBUG else "debug", seed=50,
        start_scene_number=start_scene_number, frame_collector=collector, set_trophy=True if not DEBUG else False,
        trophy_prob=0
    )  # trophy_prob=1 mean the trophy is 100% outside the box, trophy_prob=0 mean the trophy is 100% inside the box,
    metaController = MetaController(env, level)

    while env.current_scene < len(env.all_scenes) - 1:
        env.reset()
        result = metaController.execute()

        print("final reward {}".format(env.step_output.reward))

        sys.stdout.flush()
        collector.reset()