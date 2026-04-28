import torch
import torch.nn as nn
import torch.nn.functional as F

class AnisotropicConv3d(nn.Module):
    """
    3D Convolution with anisotropic kernels, useful when Z resolution is much lower than X/Y.
    E.g. ac3synapse dataset is 4x4x40nm, so Z is 10x coarser.
    We use (1, 3, 3) kernels in some places to avoid blurring across Z slices too early.
    """
    def __init__(self, in_channels, out_channels, kernel_size=(3, 3, 3), padding=(1, 1, 1)):
        super().__init__()
        self.conv = nn.Conv3d(in_channels, out_channels, kernel_size=kernel_size, padding=padding)
        self.bn = nn.BatchNorm3d(out_channels)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.relu(self.bn(self.conv(x)))

class UNet3D(nn.Module):
    """
    Lightweight 3D U-Net designed for glia segmentation on anisotropic EM data.
    """
    def __init__(self, in_channels=1, out_channels=1, init_features=16):
        super().__init__()
        
        # Encoder
        self.enc1 = self._block(in_channels, init_features)
        # Downsample primarily in XY first due to anisotropy
        self.pool1 = nn.MaxPool3d(kernel_size=(1, 2, 2), stride=(1, 2, 2))
        
        self.enc2 = self._block(init_features, init_features * 2)
        self.pool2 = nn.MaxPool3d(kernel_size=(2, 2, 2), stride=(2, 2, 2))
        
        self.enc3 = self._block(init_features * 2, init_features * 4)
        self.pool3 = nn.MaxPool3d(kernel_size=(2, 2, 2), stride=(2, 2, 2))
        
        self.bottleneck = self._block(init_features * 4, init_features * 8)
        
        # Decoder
        self.upconv3 = nn.ConvTranspose3d(init_features * 8, init_features * 4, kernel_size=(2, 2, 2), stride=(2, 2, 2))
        self.dec3 = self._block((init_features * 4) * 2, init_features * 4)
        
        self.upconv2 = nn.ConvTranspose3d(init_features * 4, init_features * 2, kernel_size=(2, 2, 2), stride=(2, 2, 2))
        self.dec2 = self._block((init_features * 2) * 2, init_features * 2)
        
        self.upconv1 = nn.ConvTranspose3d(init_features * 2, init_features, kernel_size=(1, 2, 2), stride=(1, 2, 2))
        self.dec1 = self._block(init_features * 2, init_features)
        
        self.final_conv = nn.Conv3d(init_features, out_channels, kernel_size=1)

    def _block(self, in_channels, out_channels):
        return nn.Sequential(
            AnisotropicConv3d(in_channels, out_channels, kernel_size=(3, 3, 3), padding=(1, 1, 1)),
            AnisotropicConv3d(out_channels, out_channels, kernel_size=(3, 3, 3), padding=(1, 1, 1))
        )

    def forward(self, x):
        # x is (B, C, Z, Y, X)
        enc1 = self.enc1(x)
        enc2 = self.enc2(self.pool1(enc1))
        enc3 = self.enc3(self.pool2(enc2))
        
        bottleneck = self.bottleneck(self.pool3(enc3))
        
        dec3 = self.upconv3(bottleneck)
        dec3 = torch.cat((dec3, enc3), dim=1)
        dec3 = self.dec3(dec3)
        
        dec2 = self.upconv2(dec3)
        dec2 = torch.cat((dec2, enc2), dim=1)
        dec2 = self.dec2(dec2)
        
        dec1 = self.upconv1(dec2)
        dec1 = torch.cat((dec1, enc1), dim=1)
        dec1 = self.dec1(dec1)
        
        return self.final_conv(dec1)
