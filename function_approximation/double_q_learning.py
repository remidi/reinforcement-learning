import gym
import itertools
import numpy as np
import os
import sys
import tensorflow as tf
import random
import pickle
from tensorflow.keras import models, layers

if "../" not in sys.path:
	sys.path.append("../")

from lib import plotting
env = gym.envs.make("Breakout-v0")

valid_actions = [0, 1, 2, 3]
replay_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replays')


class Estimator(object):
	def __init__(self, name='estimator'):
		self.model = self._build_model()
		self.name = name

		self.checkpoint_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'checkpoint')
		if not os.path.exists(self.checkpoint_path):
			os.mkdir(self.checkpoint_path)

		self.load_weights()

	def load_weights(self):
		model_path = os.path.join(self.checkpoint_path, '{}-model.h5'.format(self.name))
		if os.path.exists(model_path):
			self.model.load_weights(model_path)

	def set_weights(self, weights):
		self.model.set_weights(weights)

	def get_weights(self):
		return self.model.get_weights()

	@staticmethod
	def state_processor(state):
		output = tf.image.rgb_to_grayscale(state)
		# print(output.shape)
		output = tf.image.crop_to_bounding_box(output, 34, 0, 160, 160)
		output = tf.image.resize(output, [84, 84], method=tf.image.ResizeMethod.NEAREST_NEIGHBOR)
		# output = tf.squeeze(output)
		# print("output shape: {}".format(output.shape))
		return output

	@staticmethod
	def target_processor(action, target):
		A = np.zeros(shape=(action.shape[0], len(valid_actions)), dtype=float)
		A[:, action] = target
		return A

	@staticmethod
	def _build_model():
		model = tf.keras.models.Sequential()
		model.add(layers.Conv2D(32, (8, 8), strides=4, activation='relu', input_shape=(84, 84, 1)))
		model.add(layers.Conv2D(64, (4, 4), strides=2, activation='relu'))
		model.add(layers.Conv2D(64, (3, 3), strides=1, activation='relu'))
		model.add(layers.Flatten())
		model.add(layers.Dense(512))
		model.add(layers.Dense(len(valid_actions)))

		model.compile(optimizer='adam', loss='mean_squared_error', metrics=['accuracy'])
		model.summary()
		return model

	def predict(self, state):
		return self.model.predict(self.state_processor(state))

	def update(self, state, action, target):
		self.model.fit(self.state_processor(state), self.target_processor(action, target))

	def save_model(self):
		self.model.save(os.path.join(self.checkpoint_path, '{}-model.h5'.format(self.name)))


def epsilon_greedy_policy(estimator, num_actions):
	def policy(observation, epsilon):
		actions = np.ones(num_actions, dtype=float) * epsilon / num_actions
		best_action = np.argmax(estimator.predict([observation]))
		actions[best_action] += 1.0 - epsilon
		return actions

	return policy


def deep_q_learning(env,
					q_estimator,
					target_estimator,
					num_episodes,
					replay_memory_size=50000,
					replay_memory_init_size=50000,
					update_target_estimator_every=10000,
					discount_factor=0.99,
					epsilon_start=1.0,
					epsilon_end=0.1,
					epsilon_decay_steps=50000,
					batch_size=2048,
					record_video_every=50):

	steps = 0
	replay_memory = []
	stats = plotting.EpisodeStats(episode_lengths=np.zeros(num_episodes), episode_rewards=np.zeros(num_episodes))

	epsilons = np.linspace(epsilon_start, epsilon_end, epsilon_decay_steps)
	policy = epsilon_greedy_policy(q_estimator, len(valid_actions))

	state = env.reset()

	if not os.path.exists(replay_path):
		os.mkdir(replay_path)

	if os.path.exists(os.path.join(replay_path, 'replay-30000.pickle')):
		print('loading persisted pickle file')
		for p in os.listdir(replay_path):
			print('\r{}'.format(p), end='')
			sys.stdout.flush()
			with open(os.path.join(replay_path, p), 'rb') as r:
				sub_memory = pickle.load(r)
				replay_memory.extend(sub_memory)
	else:
		for i in range(1, replay_memory_init_size+1):
			print('\rreplay: {}/{}'.format(i, replay_memory_init_size), end='')
			sys.stdout.flush()
			epsilon = epsilons[min(i, epsilon_decay_steps-1)]
			action_probs = policy(state, epsilon)
			action = np.random.choice(valid_actions, p=action_probs)
			next_state, reward, done, _ = env.step(action)
			replay_memory.append((state, action, reward, next_state, done))
			if done:
				state = env.reset()
			else:
				state = next_state

			if i % 1000 == 0:
				with open(os.path.join(replay_path, 'replay-{}.pickle'.format(i)), 'wb') as f:
					pickle.dump(replay_memory[i-1000: i], f)

	print('recording experience completed.')

	# replay_memory = np.array(replay_memory)
	# replay_memory = replay_memory.reshape(replay_memory.shape[0], 1, replay_memory.shape[1])
	# print(replay_memory.shape)
	episode_rewards = np.zeros(num_episodes)
	episode_lengths = np.zeros(num_episodes)
	for episode in range(num_episodes):
		
		state = env.reset()
		
		for t in itertools.count():
			epsilon = epsilons[min(steps, epsilon_decay_steps-1)]

			if steps % update_target_estimator_every == 0:
				target_estimator.set_weights(q_estimator.get_weights())

			action_probs = policy(state, epsilon)
			action = np.random.choice(valid_actions, p=action_probs)
			next_state, reward, done, _ = env.step(action)

			if len(replay_memory) == replay_memory_size:
				replay_memory = np.delete(replay_memory, 0, 0)
			np.append(replay_memory, (state, action, reward, next_state, done))

			stats.episode_rewards[episode] += reward
			stats.episode_lengths[episode] = t

			episode_rewards[episode] += reward
			episode_lengths[episode] = t

			minibatch = random.sample(list(replay_memory), batch_size)
			states, actions, rewards, next_states, dones = map(np.array, zip(*minibatch))
			selection_actions = np.argmax(q_estimator.predict(next_states), axis=1)
			targets = rewards + discount_factor * (target_estimator.predict(next_states)[:, selection_actions])
			q_estimator.update(states, actions, targets)
			print('step: {}; episode: {}'.format(t, episode))

			if done:
				break
			state = next_state
			steps += 1

		if episode % 100 == 0:
			with open('stats.pickle', 'wb') as f:
				pickle.dump(dict(episode_rewards=episode_rewards, episode_lengths=episode_lengths), f)
			target_estimator.save_model()
			q_estimator.save_model()


	return stats


target_estimator = Estimator('target')
q_estimator = Estimator('q')

stats = deep_q_learning(env,
						q_estimator=q_estimator,
						target_estimator=target_estimator,
						num_episodes=10000, batch_size=2048)




