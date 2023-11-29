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

# # Feedforward Neural Networks for Univariate Time Series Forecasting
#
# In this notebook, we build a feedforward neural network using pytorch to forecast $\sin$ function as a time series.

import dataclasses
import math

# +
import os
from functools import cached_property
from typing import Dict, List, Tuple

import lightning as L
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from lightning.pytorch.callbacks.early_stopping import EarlyStopping
from loguru import logger
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchmetrics import MetricCollection
from torchmetrics.regression import (
    MeanAbsoluteError,
    MeanAbsolutePercentageError,
    MeanSquaredError,
    SymmetricMeanAbsolutePercentageError,
)
from ts_dl_utils.datasets.dataset import DataFrameDataset
from ts_dl_utils.datasets.pendulum import Pendulum, PendulumDataModule
from ts_dl_utils.naive_forecasters.last_observation import LastObservationForecaster

# -

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


pen = Pendulum(length=100)

df = pd.DataFrame(pen(10, 400, initial_angle=1, beta=0.001))

# Since the damping constant is very small, the data generated is mostly a sin wave.

# +
_, ax = plt.subplots(figsize=(10, 6.18))

df.plot(x="t", y="theta", ax=ax)


# -

# ## Model
#
# In this section, we create the FFN model.


# +
@dataclasses.dataclass
class TSFFNParams:
    """A dataclass to be served as our parameters for the model.

    :param hidden_widths: list of dimensions for the hidden layers
    """

    hidden_widths: List[int]


class TSFeedForward(nn.Module):
    """Feedforward networks for univaraite time series modeling.

    :param history_length: the length of the input history.
    :param horizon: the number of steps to be forecasted.
    :param ffn_params: the parameters for the FFN network.
    """

    def __init__(self, history_length: int, horizon: int, ffn_params: TSFFNParams):
        super().__init__()
        self.ffn_params = ffn_params
        self.history_length = history_length
        self.horizon = horizon

        self.regulate_input = nn.Linear(
            self.history_length, self.ffn_params.hidden_widths[0]
        )

        self.hidden_layers = nn.Sequential(
            *[
                self._linear_block(dim_in, dim_out)
                for dim_in, dim_out in zip(
                    self.ffn_params.hidden_widths[:-1],
                    self.ffn_params.hidden_widths[1:],
                )
            ]
        )

        self.regulate_output = nn.Linear(
            self.ffn_params.hidden_widths[-1], self.horizon
        )

    @property
    def ffn_config(self):
        return dataclasses.asdict(self.ffn_params)

    def _linear_block(self, dim_in, dim_out):
        return nn.Sequential(*[nn.Linear(dim_in, dim_out), nn.ReLU()])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.regulate_input(x)
        x = self.hidden_layers(x)

        return self.regulate_output(x)


# -

# ## Training

# We use [lightning](https://lightning.ai/docs/pytorch/stable/) to train our model.

# ### Training Utilities

history_length = 100
horizon = 1


# We will build a few utilities
#
# 1. To be able to feed the data into our model, we build a class (`DataFrameDataset`) that converts the pandas dataframe into a Dataset for pytorch.
# 2. To make the lightning training code simpler, we will build a [LightningDataModule](https://lightning.ai/docs/pytorch/stable/data/datamodule.html) (`PendulumDataModule`) and a [LightningModule](https://lightning.ai/docs/pytorch/stable/common/lightning_module.html) (`FFNForecaster`).


class FFNForecaster(L.LightningModule):
    def __init__(self, ffn: nn.Module):
        super().__init__()
        self.ffn = ffn

    def configure_optimizers(self):
        optimizer = torch.optim.SGD(self.parameters(), lr=1e-3)
        return optimizer

    def training_step(self, batch, batch_idx):
        x, y = batch
        x = x.squeeze().type(self.dtype)
        y = y.squeeze(-1).type(self.dtype)

        y_hat = self.ffn(x)

        loss = nn.functional.mse_loss(y_hat, y)
        self.log_dict({"train_loss": loss}, prog_bar=True)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        x = x.squeeze().type(self.dtype)
        y = y.squeeze(-1).type(self.dtype)

        y_hat = self.ffn(x)

        loss = nn.functional.mse_loss(y_hat, y)
        self.log_dict({"val_loss": loss}, prog_bar=True)
        return loss

    def predict_step(self, batch, batch_idx):
        x, y = batch
        x = x.squeeze().type(self.dtype)
        y = y.squeeze(-1).type(self.dtype)

        y_hat = self.ffn(x)
        return x, y_hat

    def forward(self, x):
        x = x.squeeze().type(self.dtype)
        return x, self.ffn(x)


# ### Data, Model and Training

# #### DataModule

ds = DataFrameDataset(dataframe=df, history_length=history_length, horizon=horizon)

len(ds)

pdm = PendulumDataModule(
    history_length=history_length,
    horizon=horizon,
    dataframe=df[["theta"]],
)

# #### LightningModule

# +
ts_ffn_params = TSFFNParams(hidden_widths=[512, 256, 64, 256, 512])

ts_ffn = TSFeedForward(
    history_length=history_length,
    horizon=horizon,
    ffn_params=ts_ffn_params,
)

ts_ffn
# -

ffn_forecaster = FFNForecaster(ffn=ts_ffn)

# #### Trainer

# +
logger = L.pytorch.loggers.TensorBoardLogger("lightning_logs", name="ffn_ts")

trainer = L.Trainer(
    precision="64",
    max_epochs=100,
    min_epochs=5,
    callbacks=[
        EarlyStopping(monitor="val_loss", mode="min", min_delta=1e-4, patience=2)
    ],
    logger=logger,
)
# -

# #### Fitting

trainer.fit(model=ffn_forecaster, datamodule=pdm)

# #### Retrieving Predictions

predictions = trainer.predict(model=ffn_forecaster, datamodule=pdm)

prediction_inputs = [i[0] for i in pdm.predict_dataloader()]
prediction_truths = [i[1].squeeze() for i in pdm.predict_dataloader()]

predictions[0][0].shape, predictions[0][1].shape

# ### Naive Forecasts
#
# To understand how good our forecasts are, we take the last observations in time and use them as forecasts.
#
# `ts_LastObservationForecaster` is a forecaster we have build for this purpose.

# +
trainer_naive = L.Trainer(precision="64")

lobs_forecaster = LastObservationForecaster(horizon=horizon)
lobs_predictions = trainer.predict(model=lobs_forecaster, datamodule=pdm)
# -

lobs_predictions[0][0].shape, lobs_predictions[0][1].shape

# ## Results

y_test_naive_pred = lobs_predictions[0][1].squeeze().detach().numpy()
y_test_pred = predictions[0][1].squeeze().detach().numpy()
y_test_truth = prediction_truths[0].numpy()

# +
fig, ax = plt.subplots(figsize=(10, 6.18))

ax.plot(y_test_truth, "g-", label="truth")

ax.plot(y_test_pred, "r--", label="predictions")

ax.plot(y_test_naive_pred, "b--", label="naive predictions")

plt.legend()
# -

# To quantify the results, we compute a few metrics.

all_metrics = MetricCollection(
    MeanAbsoluteError(),
    MeanAbsolutePercentageError(),
    MeanSquaredError(),
    SymmetricMeanAbsolutePercentageError(),
)

all_metrics(predictions[0][1].squeeze().detach(), prediction_truths[0])

# As a comparison, the naive forecast using the last observations is

all_metrics(lobs_predictions[0][1].squeeze().detach(), prediction_truths[0])
