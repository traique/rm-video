# Adapted from Bandit (https://github.com/kwatcharasupat/source-separation), Copyright (c) the Bandit authors,
# Apache-2.0 (LICENSE-Apache-2.0.txt). Modified: reduced to the inference subset
# (FreeVideo, github.com/FlashML-org/FreeVideo, Apache-2.0); rmvideo: torchaudio Spectrogram / InverseSpectrogram replaced
# by the same computation on torch.stft / torch.istft (torchaudio is no longer built for new torch versions). Same buffer
# names ("window"), so the published checkpoint loads unchanged.
from typing import Dict, Optional

import torch
from torch import nn


class _Spectrogram(nn.Module):
    """torchaudio.transforms.Spectrogram(power=None, normalized=True, pad=0): complex STFT over the last axis, divided by
    the window's L2 norm."""

    def __init__(self, n_fft, win_length, hop_length, window, center, pad_mode, onesided):
        super().__init__()
        self.n_fft, self.win_length, self.hop_length = n_fft, win_length, hop_length
        self.center, self.pad_mode, self.onesided = center, pad_mode, onesided
        self.register_buffer("window", window)

    def forward(self, x):
        shape = x.shape
        spec = torch.stft(x.reshape(-1, shape[-1]), self.n_fft, self.hop_length, self.win_length, self.window,
                          center=self.center, pad_mode=self.pad_mode, normalized=False, onesided=self.onesided,
                          return_complex=True)
        spec = spec.reshape(shape[:-1] + spec.shape[-2:])
        return spec / self.window.pow(2.0).sum().sqrt()


class _InverseSpectrogram(nn.Module):
    """torchaudio.transforms.InverseSpectrogram(normalized=True, pad=0)."""

    def __init__(self, n_fft, win_length, hop_length, window, center, onesided):
        super().__init__()
        self.n_fft, self.win_length, self.hop_length = n_fft, win_length, hop_length
        self.center, self.onesided = center, onesided
        self.register_buffer("window", window)

    def forward(self, spec, length=None):
        spec = spec * self.window.pow(2.0).sum().sqrt()
        shape = spec.shape
        wav = torch.istft(spec.reshape(-1, shape[-2], shape[-1]), self.n_fft, self.hop_length, self.win_length,
                          self.window, center=self.center, normalized=False, onesided=self.onesided, length=length,
                          return_complex=False)
        return wav.reshape(shape[:-2] + wav.shape[-1:])


class _SpectralComponent(nn.Module):
    def __init__(
            self,
            n_fft: int = 2048,
            win_length: Optional[int] = 2048,
            hop_length: int = 512,
            window_fn: str = "hann_window",
            wkwargs: Optional[Dict] = None,
            power: Optional[int] = None,
            center: bool = True,
            normalized: bool = True,
            pad_mode: str = "constant",
            onesided: bool = True,
            **kwargs,
    ) -> None:
        super().__init__()

        assert power is None and normalized is True

        win = win_length or n_fft
        window = torch.__dict__[window_fn](win, **(wkwargs or {}))
        self.stft = _Spectrogram(n_fft, win, hop_length, window, center, pad_mode, onesided)
        self.istft = _InverseSpectrogram(n_fft, win, hop_length, window, center, onesided)
