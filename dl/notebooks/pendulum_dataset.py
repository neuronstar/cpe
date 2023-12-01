# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:light
#     text_representation:
#       extension: .py
#       format_name: light
#       format_version: '1.5'
#       jupytext_version: 1.14.5
#   kernelspec:
#     display_name: deep-learning
#     language: python
#     name: deep-learning
# ---

# # A Dataset Generated by Damped Pendulum
#
# In this notebook, we demo a dataset we created to simulate the oscillations of a pendulumn.

from functools import cached_property
from typing import List, Tuple

import lightning as L
import matplotlib as mpl
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import pandas as pd
from torchmetrics import MetricCollection
from torchmetrics.regression import (
    MeanAbsoluteError,
    MeanAbsolutePercentageError,
    MeanSquaredError,
    SymmetricMeanAbsolutePercentageError,
)
from ts_dl_utils.datasets.dataset import DataFrameDataset
from ts_dl_utils.datasets.pendulum import Pendulum, PendulumDataModule
from ts_dl_utils.evaluation.evaluator import Evaluator
from ts_dl_utils.naive_forecasters.last_observation import LastObservationForecaster

# ## Data
#
# We create a dataset that models a damped pendulum. The pendulum is modelled as a damped harmonic oscillator, i.e.,
#
# $$
# \theta(t) = \theta(0) \cos(2 \pi t / p)\exp(-\beta t),
# $$
#
# where $\theta(t)$ is the angle of the pendulum at time $t$.
# The period $p$ is calculated using
#
# $$
# p = 2 \pi \sqrt(L / g),
# $$
#
# with $L$ being the length of the pendulum
# and $g$ being the surface gravity.


pen = Pendulum(length=200)

df = pd.DataFrame(
    pen(num_periods=5, num_samples_per_period=100, initial_angle=1, beta=0.01)
)

# Since the damping constant is very small, the data generated is mostly a sin wave.

# +
_, ax = plt.subplots(figsize=(10, 6.18))

df.plot(x="t", y="theta", ax=ax)


# -

# ### PyTorch and Lighting DataModule

history_length = 100
horizon = 5

ds = DataFrameDataset(dataframe=df, history_length=history_length, horizon=horizon)

print(
    f"""
    There were {len(df)} rows in the dataframe\n
    We got {len(ds)} data points in the dataset (history length: {history_length}, horizon: {horizon})
    """
)

# We can create a [LightningDataModule for Lightning](https://lightning.ai/docs/pytorch/stable/data/datamodule.html). When training/evaluating using Lightning, we only need to pass this object `pdm` to the training.

pdm = PendulumDataModule(
    history_length=history_length, horizon=horizon, dataframe=df[["theta"]]
)

# ## Naive Forecasts

prediction_truths = [i[1].squeeze() for i in pdm.predict_dataloader()]

# +
trainer_naive = L.Trainer(precision="64")

lobs_forecaster = LastObservationForecaster(horizon=horizon)
lobs_predictions = trainer_naive.predict(model=lobs_forecaster, datamodule=pdm)
# -

evaluator = Evaluator(step=0)

# +
fig, ax = plt.subplots(figsize=(10, 6.18))

ax.plot(
    evaluator.y_true(dataloader=pdm.predict_dataloader()),
    "g-",
    label="truth",
)

ax.plot(evaluator.y(lobs_predictions), "b-.", label="naive predictions")

plt.legend()
# -

evaluator.metrics(lobs_predictions, pdm.predict_dataloader())

# Naive forecaster works well since we do not have dramatic changes between two time steps.

# ## Delayed Embedding

ds_de = DataFrameDataset(dataframe=df["theta"][:200], history_length=1, horizon=1)


# +
class DelayedEmbeddingAnimation:
    def __init__(
        self, dataset: DataFrameDataset, fig: mpl.figure.Figure, ax: mpl.axes.Axes
    ):
        self.dataset = dataset
        self.ax = ax
        self.fig = fig

    @cached_property
    def data(self) -> List[Tuple[float, float]]:
        return [(i[0][0], i[1][0]) for i in self.dataset]

    @cached_property
    def x(self):
        return [i[0] for i in self.data]

    @cached_property
    def y(self):
        return [i[1] for i in self.data]

    def data_gen(self):
        for i in self.data:
            yield i

    def animation_init(self) -> mpl.axes.Axes:
        ax.plot(
            self.x,
            self.y,
        )
        ax.set_xlim([-1.1, 1.1])
        ax.set_ylim([-1.1, 1.1])
        ax.set_xlabel("t")
        ax.set_ylabel("t+1")

        return self.ax

    def animation_run(self, data: Tuple[float, float]) -> mpl.axes.Axes:
        x, y = data
        self.ax.scatter(x, y)
        return self.ax

    @cached_property
    def time_steps(self):
        return len(self.data)

    def build(self, interval: int = 10, save_count: int = 10):
        return animation.FuncAnimation(
            self.fig,
            self.animation_run,
            self.data_gen,
            interval=interval,
            init_func=self.animation_init,
            save_count=save_count,
        )


fig, ax = plt.subplots(figsize=(10, 10))

dea = DelayedEmbeddingAnimation(dataset=ds_de, fig=fig, ax=ax)

ani = dea.build(interval=10, save_count=dea.time_steps)

gif_writer = animation.PillowWriter(fps=15, metadata=dict(artist="Me"), bitrate=1800)

ani.save("results/pendulum_dataset/delayed_embedding_animation.gif", writer=gif_writer)
# ani.save("results/pendulum_dataset/delayed_embedding_animation.mp4")
# -
