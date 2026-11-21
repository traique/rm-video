# Adapted from Bandit (https://github.com/kwatcharasupat/source-separation), Copyright (c) the Bandit authors,
# Apache-2.0 (LICENSE-Apache-2.0.txt). Modified: reduced to the inference subset
# (FreeVideo, github.com/FlashML-org/FreeVideo, Apache-2.0), copied unchanged into rmvideo.
from abc import ABC
from typing import Iterable, Mapping, Union

from torch import nn



class BandsplitCoreBase(nn.Module, ABC):
    band_split: nn.Module
    tf_model: nn.Module
    mask_estim: Union[nn.Module, Mapping[str, nn.Module], Iterable[nn.Module]]

    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def mask(x, m):
        return x * m
