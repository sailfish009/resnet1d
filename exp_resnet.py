"""
resnet for signal data, pytorch version

Shenda Hong, Oct 2019
"""

import numpy as np
from collections import Counter
from tqdm import tqdm
from matplotlib import pyplot as plt

from util import *

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

class MyDataset(Dataset):
    def __init__(self, data, label):
        self.data = data
        self.label = label

    def __getitem__(self, index):
        return (torch.tensor(self.data[index], dtype=torch.float), torch.tensor(self.label[index], dtype=torch.long))

    def __len__(self):
        return len(self.data)
    
class MyConv1dPadSame(nn.Module):
    """
    extend nn.Conv1d to support SAME padding
    """
    def __init__(self, in_channels, out_channels, kernel_size, stride):
        super(MyConv1dPadSame, self).__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride

    def forward(self, x):
        in_dim = x.shape[-1]
        out_dim = (in_dim + self.stride - 1) // self.stride
        p = max(0, (out_dim - 1) * self.stride + self.kernel_size - in_dim)
        pad_left = p // 2
        pad_right = p - pad_left
        net = torch.nn.Sequential(
            nn.ConstantPad1d((pad_left, pad_right), 0),
            torch.nn.Conv1d(in_channels=self.in_channels, out_channels=self.out_channels, kernel_size=self.kernel_size, stride=self.stride))
        return net(x)
        
class MyMaxPool1dPadSame(nn.Module):
    """
    extend nn.MaxPool1d to support SAME padding
    """
    def __init__(self, kernel_size):
        super(MyMaxPool1dPadSame, self).__init__()
        self.kernel_size = kernel_size
        self.stride = 1

    def forward(self, x):
        in_dim = x.shape[-1]
        out_dim = (in_dim + self.stride - 1) // self.stride
        p = max(0, (out_dim - 1) * self.stride + self.kernel_size - in_dim)
        pad_left = p // 2
        pad_right = p - pad_left
        net = torch.nn.Sequential(
            nn.ConstantPad1d((pad_left, pad_right), 0),
            torch.nn.MaxPool1d(kernel_size=self.kernel_size))
        return net(x)    
    
class Bottleneck(nn.Module):
    """
    ResNet Bottleneck Block
    """
    def __init__(self, in_channels, out_channels, kernel_size, stride, downsample, is_first_block=False):
        super(Bottleneck, self).__init__()
        
        self.in_channels = in_channels
        self.kernel_size = kernel_size
        self.out_channels = out_channels
        self.stride = stride
        self.downsample = downsample
        self.is_first_block = is_first_block

        # the first conv
        self.bn1 = nn.BatchNorm1d(in_channels)
        self.relu1 = nn.ReLU()
        self.do1 = nn.Dropout(p=0.5)
        if self.downsample:
            # if downsample, set stride > 1
            self.conv1 = MyConv1dPadSame(in_channels=in_channels, out_channels=out_channels, kernel_size=kernel_size, stride=self.stride)
        else:
            # if not downsample, set stride = 1
            self.conv1 = MyConv1dPadSame(in_channels=in_channels, out_channels=out_channels, kernel_size=kernel_size, stride=1)

        # the second conv
        self.bn2 = nn.BatchNorm1d(out_channels)
        self.relu2 = nn.ReLU()
        self.do2 = nn.Dropout()
        self.conv2 = MyConv1dPadSame(in_channels=out_channels, out_channels=out_channels, kernel_size=kernel_size, stride=1)
                
        self.max_pool = MyMaxPool1dPadSame(kernel_size=self.stride)

    def forward(self, x):
        
        identity = x
        
        # the first conv
        out = x
        if self.is_first_block:
            out = self.bn1(out)
            out = self.relu1(out)
            out = self.do1(out)
        out = self.conv1(out)
        
        # the second conv
        out = self.bn2(out)
        out = self.relu2(out)
        out = self.do2(out)
        out = self.conv2(out)
        
        # if downsample, also downsample identity
        if self.downsample:
            identity = self.max_pool(identity)
            
        # if expand channel, also pad zeros to identity
        if self.out_channels != self.in_channels:
            identity = identity.transpose(-1,-2)
            ch1 = (self.out_channels-self.in_channels)//2
            ch2 = self.out_channels-self.in_channels-ch1
            identity = nn.ConstantPad1d((ch1, ch2), 0)(identity)
            identity = identity.transpose(-1,-2)
        
        # shortcut
        out += identity

        return out
    
class ResNet(nn.Module):
    """
    input:
        X: (n_samples, n_channel, n_length)
        Y: (n_samples)
    output:
        out: (n_samples)
    """

    def __init__(self, in_channels, base_filters, kernel_size, stride, n_block, n_classes, verbose=False):
        super(ResNet, self).__init__()
        
        self.verbose = verbose
        self.n_block = n_block
        self.kernel_size = kernel_size
        self.stride = stride

        # first block
        self.first_block_conv = MyConv1dPadSame(in_channels=in_channels, out_channels=base_filters, kernel_size=self.kernel_size, stride=1)
        self.first_block_bn = nn.BatchNorm1d(base_filters)
        self.first_block_relu = nn.ReLU()
                
        # residual blocks
        self.bottleneck_list = []
        for i_block in range(self.n_block):
            # is_first_block
            if i_block == 0:
                is_first_block = True
            else:
                is_first_block = False
            # downsample
            if i_block % 2 == 1:
                downsample = True
            else:
                downsample = False
            # in_channels and out_channels
            if is_first_block:
                in_channels = base_filters
                out_channels = in_channels
            else:
                in_channels = int(base_filters*2**((i_block-1)//2))
                if downsample:
                    out_channels = in_channels
                else:
                    out_channels = in_channels * 2
            
            tmp_block = Bottleneck(in_channels=in_channels, out_channels=out_channels, kernel_size=self.kernel_size, stride = self.stride, downsample=downsample, is_first_block=is_first_block)
            self.bottleneck_list.append(tmp_block)

        # final prediction
        self.final_bn = nn.BatchNorm1d(out_channels)
        self.final_relu = nn.ReLU()
        self.dense = nn.Linear(out_channels, n_classes)
        self.softmax = nn.Softmax(dim=1)
        
    def forward(self, x):
        
        out = x
        
        # first block
        if self.verbose:
            print('input shape', out.shape)
        out = self.first_block_conv(out)
        if self.verbose:
            print('after first conv', out.shape)
        out = self.first_block_bn(out)
        out = self.first_block_relu(out)
        
        # residual blocks, every bottleneck has two conv
        for i_block in range(self.n_block):
            net = self.bottleneck_list[i_block]
            if self.verbose:
                print('i_block: {0}, in_channels: {1}, out_channels: {2}, downsample: {3}'.format(i_block, net.in_channels, net.out_channels, net.downsample))
            out = net(out)
            if self.verbose:
                print(out.shape)

        # final prediction
        out = self.final_bn(out)
        out = self.final_relu(out)
        out = out.mean(-1)
        out = self.dense(out)
        out = self.softmax(out)
        if self.verbose:
            print('softmax', out.shape)
        
        return out    
    
if __name__ == "__main__":
    
    data, label = read_data_generated(n_samples=100, n_length=200, n_channel=3, n_classes=3)
    print(data.shape, Counter(label))
    
#     dataset = MyDataset(data, label)
#     dataloader = DataLoader(dataset, batch_size=5)
    
#     device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
#     model = ResNet(in_channels=12, base_filters=64, kernel_size=16, stride=3, n_block=10, n_classes=3)
#     model.to(device)

#     optimizer = optim.Adam(model.parameters(), lr=1e-3)
#     loss_func = torch.nn.CrossEntropyLoss()
    
#     prog_iter = tqdm(dataloader, desc="Training", leave=False)
#     for batch_idx, batch in enumerate(prog_iter):

#         input_x, input_y = tuple(t.to(device) for t in batch)
#         pred = model(input_x)

#         loss = loss_func(pred, input_y)
#         optimizer.zero_grad()
#         loss.backward()
#         optimizer.step()
#         print(loss)
    
    
#         break
    
    
    
    
    