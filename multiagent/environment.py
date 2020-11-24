import gym
from gym import spaces
from gym.envs.registration import EnvSpec
from multiagent.core import Landmark
import numpy as np
from multiagent.multi_discrete import MultiDiscrete

import abc

class MultiAgentBase(abc.ABC):
    @abc.abstractmethod
    def step(self, actions):
        """Returns reward, terminated, info."""
        pass

    @abc.abstractmethod
    def get_obs(self):
        """Returns all agent observations in a list."""
        pass

    @abc.abstractmethod
    def get_obs_agent(self, agent_id):
        """Returns observation for agent_id."""
        pass

    @abc.abstractmethod
    def get_obs_size(self):
        """Returns the size of the observation."""
        pass

    @abc.abstractmethod
    def get_state(self):
        """Returns the global state."""
        pass

    @abc.abstractmethod
    def get_state_size(self):
        """Returns the size of the global state."""
        pass

    @abc.abstractmethod
    def get_avail_actions(self):
        """Returns the available actions of all agents in a list."""
        pass

    @abc.abstractmethod
    def get_avail_agent_actions(self, agent_id):
        """Returns the available actions for agent_id."""
        pass

    @abc.abstractmethod
    def get_total_actions(self):
        """Returns the total number of actions an agent could ever take."""
        pass

    @abc.abstractmethod
    def reset(self):
        """Returns initial observations and states."""
        pass

    @abc.abstractmethod
    def render(self):
        pass

    @abc.abstractmethod
    def close(self):
        pass

    @abc.abstractmethod
    def seed(self):
        pass

    @abc.abstractmethod
    def save_replay(self):
        """Save a replay."""
        pass

    def get_env_info(self):
        env_info = {"state_shape": self.get_state_size(),
                    "obs_shape": self.get_obs_size(),
                    "n_actions": self.get_total_actions(),
                    "n_agents": self.n_agents,
                    "episode_limit": self.episode_limit}
        return env_info





# environment for all agents in the multiagent world
# currently code assumes that no agents will be created/destroyed at runtime!
class MultiAgentEnv(MultiAgentBase):
    metadata = {
        'render.modes' : ['human', 'rgb_array']
    }

    def __init__(self, world, reset_callback=None, reward_callback=None,
                 observation_callback=None, info_callback=None,
                 done_callback=None, shared_viewer=True, discrete_action_space=True, discrete_action_input=True, time_limit=25, **kwargs):

        self.world = world
        self.agents = self.world.policy_agents
        # set required vectorized gym env property
        self.n = len(world.policy_agents)
        # scenario callbacks
        self.reset_callback = reset_callback
        self.reward_callback = reward_callback
        self.observation_callback = observation_callback
        self.info_callback = info_callback
        self.done_callback = done_callback
        # environment parameters
        self.discrete_action_space = discrete_action_space
        # if true, action is a number 0...N, otherwise action is a one-hot N-dimensional vector
        self.discrete_action_input = discrete_action_input
        # if true, even the action is continuous, action will be performed discretely
        self.force_discrete_action = world.discrete_action if hasattr(world, 'discrete_action') else False
        # if true, every agent has the same reward
        self.shared_reward = world.collaborative if hasattr(world, 'collaborative') else False
        self.time = 0.0
        self.time_limit = time_limit
        self.num_episodes = 0

        # configure spaces
        self.action_space = []
        self.observation_space = []
        for agent in self.agents:
            total_action_space = []
            # physical action space
            if self.discrete_action_space:
                u_action_space = spaces.Discrete(world.dim_p * 2 + 1)
            else:
                u_action_space = spaces.Box(low=-agent.u_range, high=+agent.u_range, shape=(world.dim_p,), dtype=np.float32)
            if agent.movable:
                total_action_space.append(u_action_space)
            # communication action space
            if self.discrete_action_space:
                c_action_space = spaces.Discrete(world.dim_c)
            else:
                c_action_space = spaces.Box(low=0.0, high=1.0, shape=(world.dim_c,), dtype=np.float32)
            if not agent.silent:
                total_action_space.append(c_action_space)
            # total action space
            if len(total_action_space) > 1:
                # all action spaces are discrete, so simplify to MultiDiscrete action space
                if all([isinstance(act_space, spaces.Discrete) for act_space in total_action_space]):
                    act_space = MultiDiscrete([[0, act_space.n - 1] for act_space in total_action_space])
                else:
                    act_space = spaces.Tuple(total_action_space)
                self.action_space.append(act_space)
            else:
                self.action_space.append(total_action_space[0])
            # observation space
            obs_dim = len(observation_callback(agent, self.world))
            self.observation_space.append(spaces.Box(low=-np.inf, high=+np.inf, shape=(obs_dim,), dtype=np.float32))
            agent.action.c = np.zeros(self.world.dim_c)

        # rendering
        self.shared_viewer = shared_viewer
        if self.shared_viewer:
            self.viewers = [None]
        else:
            self.viewers = [None] * self.n
        self._reset_render()

        self.obs_n = [None] * self.n
        self.n_agents = self.n
        self.translate_observation = None
        self.translate_state = None

    def step(self, action_n):
        obs_n = []
        reward_n = []
        done_n = []
        info_n = {'n': []}
        self.agents = self.world.policy_agents
        # set action for each agent
        for i, agent in enumerate(self.agents):
            self._set_action(action_n[i], agent, self.action_space[i])
        # advance world state
        self.world.step()
        # record observation for each agent
        for agent in self.agents:
            obs_n.append(self._get_obs(agent))
            reward_n.append(self._get_reward(agent))
            done_n.append(self._get_done(agent))

            info_n['n'].append(self._get_info(agent))

        # all agents get total reward in cooperative case
        reward = np.sum(reward_n)
        if self.shared_reward:
            reward_n = [reward] * self.n

        self.obs_n = obs_n
        self.time += 1

        if self.time == self.time_limit:
            done = 1.0
        else:
            done = 0.0

        return reward, done, info_n

    def reset(self, evaluate=False):
        # reset world
        self.reset_callback(self.world)
        # reset renderer
        self._reset_render()
        # record observations for each agent
        obs_n = []
        self.agents = self.world.policy_agents
        self.time = 0.0

        if not evaluate:
            self.num_episodes += 1

        return self.get_obs()

    # get info used for benchmarking
    def _get_info(self, agent):
        if self.info_callback is None:
            return {}
        return self.info_callback(agent, self.world)

    # get observation for a particular agent
    def _get_obs(self, agent):
        if self.observation_callback is None:
            return np.zeros(0)
        return self.observation_callback(agent, self.world)

    # get dones for a particular agent
    # unused right now -- agents are allowed to go beyond the viewing screen
    def _get_done(self, agent):
        if self.done_callback is None:
            return False
        return self.done_callback(agent, self.world)

    # get reward for a particular agent
    def _get_reward(self, agent):
        if self.reward_callback is None:
            return 0.0
        return self.reward_callback(agent, self.world)

    # set env action for a particular agent
    def _set_action(self, action, agent, action_space, time=None):
        agent.action.u = np.zeros(self.world.dim_p)
        agent.action.c = np.zeros(self.world.dim_c)
        # process action
        if isinstance(action_space, MultiDiscrete):
            act = []
            size = action_space.high - action_space.low + 1
            index = 0
            for s in size:
                act.append(action[index:(index+s)])
                index += s
            action = act
        else:
            action = [action]

        if agent.movable:
            # physical action
            if self.discrete_action_input:
                agent.action.u = np.zeros(self.world.dim_p)
                # process discrete action
                if action[0] == 1: agent.action.u[0] = -1.0
                if action[0] == 2: agent.action.u[0] = +1.0
                if action[0] == 3: agent.action.u[1] = -1.0
                if action[0] == 4: agent.action.u[1] = +1.0
            else:
                if self.force_discrete_action:
                    d = np.argmax(action[0])
                    action[0][:] = 0.0
                    action[0][d] = 1.0
                if self.discrete_action_space:
                    agent.action.u[0] += action[0][1] - action[0][2]
                    agent.action.u[1] += action[0][3] - action[0][4]
                else:
                    agent.action.u = action[0]
            sensitivity = 5.0
            if agent.accel is not None:
                sensitivity = agent.accel
            agent.action.u *= sensitivity
            action = action[1:]
        if not agent.silent:
            # communication action
            if self.discrete_action_input:
                agent.action.c = np.zeros(self.world.dim_c)
                agent.action.c[action[0]] = 1.0
            else:
                agent.action.c = action[0]
            action = action[1:]
        # make sure we used all elements of action
        assert len(action) == 0

    # reset rendering assets
    def _reset_render(self):
        self.render_geoms = None
        self.render_geoms_xform = None

    # render environment
    def render(self, mode='human'):
        if mode == 'human':
            alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            message = ''
            for agent in self.world.agents:
                comm = []
                for other in self.world.agents:
                    if other is agent: continue
                    if np.all(other.state.c == 0):
                        word = '_'
                    else:
                        word = alphabet[np.argmax(other.state.c)]
                    message += (other.name + ' to ' + agent.name + ': ' + word + '   ')
            print(message)

        for i in range(len(self.viewers)):
            # create viewers (if necessary)
            if self.viewers[i] is None:
                # import rendering only if we need it (and don't import for headless machines)
                #from gym.envs.classic_control import rendering
                from multiagent import rendering
                self.viewers[i] = rendering.Viewer(700,700)

        # create rendering geometry
        if self.render_geoms is None:
            # import rendering only if we need it (and don't import for headless machines)
            #from gym.envs.classic_control import rendering
            from multiagent import rendering
            self.render_geoms = []
            self.render_geoms_xform = []
            for entity in self.world.entities:
                geom = rendering.make_circle(entity.size)
                xform = rendering.Transform()
                if 'agent' in entity.name:
                    geom.set_color(*entity.color, alpha=0.5)
                else:
                    geom.set_color(*entity.color)
                geom.add_attr(xform)
                self.render_geoms.append(geom)
                self.render_geoms_xform.append(xform)

            # add geoms to viewer
            for viewer in self.viewers:
                viewer.geoms = []
                for geom in self.render_geoms:
                    viewer.add_geom(geom)

        results = []
        for i in range(len(self.viewers)):
            from multiagent import rendering
            # update bounds to center around agent
            cam_range = 1
            if self.shared_viewer:
                pos = np.zeros(self.world.dim_p)
            else:
                pos = self.agents[i].state.p_pos
            self.viewers[i].set_bounds(pos[0]-cam_range,pos[0]+cam_range,pos[1]-cam_range,pos[1]+cam_range)
            # update geometry positions
            for e, entity in enumerate(self.world.entities):
                self.render_geoms_xform[e].set_translation(*entity.state.p_pos)
            # render to display or array
            results.append(self.viewers[i].render(return_rgb_array = mode=='rgb_array'))

        return results

    # create receptor field locations in local coordinate frame
    def _make_receptor_locations(self, agent):
        receptor_type = 'polar'
        range_min = 0.05 * 2.0
        range_max = 1.00
        dx = []
        # circular receptive field
        if receptor_type == 'polar':
            for angle in np.linspace(-np.pi, +np.pi, 8, endpoint=False):
                for distance in np.linspace(range_min, range_max, 3):
                    dx.append(distance * np.array([np.cos(angle), np.sin(angle)]))
            # add origin
            dx.append(np.array([0.0, 0.0]))
        # grid receptive field
        if receptor_type == 'grid':
            for x in np.linspace(-range_max, +range_max, 5):
                for y in np.linspace(-range_max, +range_max, 5):
                    dx.append(np.array([x,y]))
        return dx

    def get_obs(self, translate=None):
        return [self.get_obs_agent(agent_id) for agent_id, agent in enumerate(self.agents)]

    def get_obs_agent(self, agent_id, structure=False):
        agent = self.world.agents[agent_id]
        obs = [agent.state.p_pos, agent.state.p_vel]
        obs_structure = [0, 4] # p_pos + p_vel = 4

        for i, a in enumerate(self.world.agents):
            if i != agent_id:
                obs.append(agent.state.p_pos - a.state.p_pos)

        obs_structure.append(2*(len(self.world.agents)-1))

        for landmark in self.world.landmarks:
            obs.append(landmark.state.p_pos - agent.state.p_pos)

        obs_structure.append(2*(len(self.world.landmarks)))

        if structure:
            return np.cumsum(obs_structure)
        elif self.translate_observation is not None:
            obs_structure = np.cumsum(obs_structure)
            obs = np.concatenate(obs, axis=0)
            new_obs = np.zeros(self.translate_observation[-1])

            for i in range(len(self.translate_observation)-1):
                obs_size = obs_structure[i+1]-obs_structure[i]
                new_obs[self.translate_observation[i]:self.translate_observation[i]+obs_size] = obs[obs_structure[i]:obs_structure[i+1]]

            return new_obs

        else:
            return np.concatenate(obs, axis=0)

    def get_obs_size(self):
        return self.get_obs_agent(0).shape[0]

    def get_state(self, structure=False, translate=None):
        state = []
        state_structure = [0]

        for agent in self.world.agents:
            state.append(agent.state.p_pos)
            state.append(agent.state.p_vel)

        state_structure.append(len(self.world.agents)*4)
        for landmark in self.world.landmarks:
            state.append(landmark.state.p_pos)

        state_structure.append(len(self.world.agents)*2)

        state = np.concatenate(state, axis=0)
        assert len(state.shape) == 1

        if structure:
            return np.cumsum(state_structure)
        elif self.translate_state is not None:
            state_structure = np.cumsum(state_structure)
            new_state = np.zeros(self.translate_state[-1])

            for i in range(len(self.translate_state) - 1):
                state_size = state_structure[i+1]-state_structure[i]
                new_state[self.translate_state[i]:self.translate_state[i] + state_size] = state[ state_structure[ i]: state_structure[ i + 1]]

            return new_state
        else:
            return state

    def get_state_size(self):
        return self.get_state().shape[0]

    def get_avail_actions(self):
        return [np.arange(5) for _ in self.agents]

    def get_avail_agent_actions(self, agent_id):
        return np.arange(5)

    def get_total_actions(self):
        if self.discrete_action_space:
            return 5
        else:
            raise ValueError('continuous action space not supported')

    def close(self):
        pass

    def seed(self):
        pass

    def save_replay(self):
        pass


# vectorized wrapper for a batch of multi-agent environments
# assumes all environments have the same observation and action space
class BatchMultiAgentEnv(gym.Env):
    metadata = {
        'runtime.vectorized': True,
        'render.modes' : ['human', 'rgb_array']
    }

    def __init__(self, env_batch):
        self.env_batch = env_batch

    @property
    def n(self):
        return np.sum([env.n for env in self.env_batch])

    @property
    def action_space(self):
        return self.env_batch[0].action_space

    @property
    def observation_space(self):
        return self.env_batch[0].observation_space

    def step(self, action_n, time):
        obs_n = []
        reward_n = []
        done_n = []
        info_n = {'n': []}
        i = 0
        for env in self.env_batch:
            obs, reward, done, _ = env.step(action_n[i:(i+env.n)], time)
            i += env.n
            obs_n += obs
            # reward = [r / len(self.env_batch) for r in reward]
            reward_n += reward
            done_n += done
        return obs_n, reward_n, done_n, info_n

    def reset(self):
        obs_n = []
        for env in self.env_batch:
            obs_n += env.reset()
        return obs_n

    # render environment
    def render(self, mode='human', close=True):
        results_n = []
        for env in self.env_batch:
            results_n += env.render(mode, close)
        return results_n
