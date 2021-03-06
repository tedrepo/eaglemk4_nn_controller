# Copyright (c) 2018 Roma Sokolkov
# MIT License

'''
Main controller loop.
'''

import gym
import os
import numpy as np
import time
import rospy

import eaglemk4_nn_controller.gym  # noqa: F401
from stable_baselines.ddpg.policies import LnMlpPolicy
from stable_baselines.ddpg.noise import OrnsteinUhlenbeckActionNoise
from eaglemk4_nn_controller.models.ddpg_with_vae import DDPGWithVAE as DDPG
from eaglemk4_nn_controller.models.vae.controller import VAEController


def print_grn(s):
    GRN = '\033[32m'
    END = '\033[0m'
    print(GRN + s + END)


class Controller:

    HZ = 20.0

    PATH_MODEL_DDPG = "ddpg.pkl"
    PATH_MODEL_VAE = "vae.ckpt"

    def __init__(self):
        # Make sure model path exists.
        self.model_path = rospy.get_param('nn_controller/model_path',
                                          '/data/models')
        if not os.path.exists(self.model_path):
            raise Exception(self.model_path + ' does not exist.')
        self.ddpg_path = os.path.join(self.model_path, self.PATH_MODEL_DDPG)
        self.vae_path = os.path.join(self.model_path, self.PATH_MODEL_VAE)

        self.env = gym.make('eaglemk4-v0')

        self.vae_batch_size = \
            rospy.get_param('nn_controller/vae_batch_size', 64)
        self.vae_buffer_size = \
            rospy.get_param('nn_controller/vae_buffer_size', 500)
        self.vae_epoch_number = \
            rospy.get_param('nn_controller/vae_epoch_number', 10)

        im_h = rospy.get_param('nn_controller/image_height', 80)
        im_w = rospy.get_param('nn_controller/image_width', 160)
        im_c = rospy.get_param('nn_controller/image_channels', 3)
        self.image_size = (im_h, im_w, im_c)

        self.ddpg_batch_size = \
            rospy.get_param('nn_controller/ddpg_batch_size', 64)
        self.ddpg_memory_size = \
            rospy.get_param('nn_controller/ddpg_memory_size', 1000)
        self.ddpg_training_steps = \
            rospy.get_param('nn_controller/ddpg_training_steps', 300)
        self.ddpg_skip_episodes = \
            rospy.get_param('nn_controller/ddpg_skip_episodes', 5)

        # Initialize VAE model and add it to gym environment.
        # VAE does image post processing to latent vector and
        # buffers raw image for future optimization.
        self.vae = VAEController(buffer_size=self.vae_buffer_size,
                                 image_size=self.image_size,
                                 batch_size=self.vae_batch_size,
                                 epoch_per_optimization=self.vae_epoch_number)
        self.env.unwrapped.set_vae(self.vae)

        if self._any_precompiled_models():
            self.ddpg = DDPG.load(self.ddpg_path, self.env)
            self.vae.load(self.vae_path)
            self.ddpg_skip_episodes = 0
            print("Loaded precompiled models from ", self.model_path)
        else:
            self.ddpg = self._init_ddpg()
            print("Initialized empty models.")

        # Don't run anything until human approves.
        print("EagleMK4 Neural Network Controller is loaded!")
        print("1. Press triangle to select a task.")
        print("2. Press right bumper to start a task.")
        self._wait_autopilot()

        # Run infinite loop.
        self.run()

    def _init_ddpg(self):
        # the noise objects for DDPG
        n_actions = self.env.action_space.shape[-1]
        action_noise = OrnsteinUhlenbeckActionNoise(
                mean=np.zeros(n_actions),
                theta=float(0.6) * np.ones(n_actions),
                sigma=float(0.2) * np.ones(n_actions)
                )

        return DDPG(LnMlpPolicy,
                    self.env,
                    verbose=1,
                    batch_size=self.ddpg_batch_size,
                    clip_norm=5e-3,
                    gamma=0.9,
                    param_noise=None,
                    action_noise=action_noise,
                    memory_limit=self.ddpg_memory_size,
                    nb_train_steps=self.ddpg_training_steps,
                    )

    def run(self):
        while True:
            # We have only two tasks at the moment:
            # - train - runs online training.
            # - test - evaluates trained models.
            if self.env.unwrapped.is_training():
                self.run_training()
            elif self.env.unwrapped.is_testing():
                self.run_testing()

    def run_training(self):
        episode = 1
        do_ddpg_training = False

        print_grn("Training started")
        while self.env.unwrapped.is_training():
            if episode > self.ddpg_skip_episodes:
                do_ddpg_training = True
            self.ddpg.learn(vae=self.vae,
                            do_ddpg_training=do_ddpg_training)
            episode += 1
            print_grn("Ready for new episode")
            self._wait_autopilot()

        # Finally save model files.
        self.ddpg.save(self.ddpg_path)
        self.vae.save(self.vae_path)
        print_grn("Training finished")

    def run_testing(self):
        print_grn("Testing started")
        if self._any_precompiled_models():
            # Reload models and run testing episodes.
            self.ddpg = DDPG.load(self.ddpg_path, self.env)
            self.vae.load(self.vae_path)
            while self.env.unwrapped.is_testing():
                # Reset will wait for autopilot mode ("right bumper" pressed).
                obs = self.env.reset()
                while True:
                    time.sleep(1.0 / self.HZ)
                    action, _states = self.ddpg.predict(obs)
                    obs, reward, done, info = self.env.step(action)
                    print(action)
                    if done:
                        print("Testing episode finished.")
                        break
        else:
            print("No precompiled models found.",
                  "Please run training by pressing triange",
                  "and press-keep right bumpe."
                  "Unpressing right bumper stops the episode.")
            while self.env.unwrapped.is_testing():
                time.sleep(1.0 / self.HZ)
        print_grn("Testing finished")

    def _any_precompiled_models(self):
        if os.path.exists(self.ddpg_path) and \
           os.path.exists(self.vae_path):
            return True
        return False

    # Make sure user pressed autopilot button.
    def _wait_autopilot(self):
        while True:
            time.sleep(1 / self.HZ)
            if self.env.unwrapped.is_autopilot():
                return

    def close(self):
        return
