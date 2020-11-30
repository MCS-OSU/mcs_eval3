import os
import numpy as np

import torch
from torch.nn import functional as F

from vision.instSeg import data
from vision.instSeg.dvis_network import DVIS
from vision.instSeg.utils.augmentations import BaseTransform

class MaskAndClassPredictor(object):
    '''
    The class is used to load trained DVIS-MC model and predict panoptic segmentation from raw data (RGB or RGB+D)
    '''
    def __init__(self, dataset='mcsvideo3_inter',
                       config='plus_resnet50_config_depth_MC',
                       weights=None, cuda=True):
        '''
        @Param: dataset -- 'mcsvideo3_inter | mcsvideo_inter | mcsvideo_voe'
                config -- check the config files in data for more configurations.
                weights -- file for loading model weights
        '''
        cfg, set_cfg = data.dataset_specific_import(dataset)
        set_cfg(cfg, config)

        self.fg_stCh   = cfg.dataset.sem_fg_stCH
        self.transform = BaseTransform(cfg, resize_gt=True)
        self.net       = DVIS(cfg)

        if weights is None:
            weights = './vision/instSeg/dvis_'+config.split('_')[1]+'_mc.pth'
            if not os.path.exists(weights):
                print('Please get the weights file ready to use the model')
        self.net.load_weights(weights)
        self.net.eval()

        self.cuda    = cuda
        if self.cuda:
            self.net = self.net.cuda()

    def transform_input(self, bgrI, depthI=None):
        '''
        @Func: image transform, mainly normalization and resize if needed.
        '''
        height, width = bgrI.shape[:2]

        # construct virtual mask and target to match transform API
        num_crowds = 0
        masks      = np.zeros([1, height, width], dtype=np.float)
        target     = np.array([[0,0,1,1,0]])
        if depthI is not None:
            assert(bgrI.shape[0] == depthI.shape[0] and bgrI.shape[1]==depthI.shape[1])
            num_crowds = 1
            masks      = np.concatenate([masks, depthI[None, :, :]], axis=0)
            target     = np.concatenate([target, np.asarray([[0,0,1,1,-1]])], axis=0)

        # transform
        img, masks, boxes, labels = self.transform(bgrI, masks, target[:, :4],
                                               {'num_crowds': num_crowds, 'labels': target[:, 4]})

        # concate depthI (if have) to bgrI as network input
        if depthI is not None:
            depthI = np.transpose(masks[-1:], [1,2,0]).astype(np.float32)
            depthI = (depthI-153.)/64.
            img    = np.concatenate([img, depthI], axis=-1)
        return img

    def step(self, bgrI, depthI=None):
        '''
        @Param: bgrI -- [ht, wd, 3] in 'BGR' color space
                depthI -- [ht, wd] with value 0 - 255
        '''
        height, width = bgrI.shape[:2]
        normI = self.transform_input(bgrI, depthI) # [ht, wd, ch]
        batch = torch.from_numpy(normI[None, ...]).permute(0, 3, 1, 2) # [1, ch, ht, wd]
        if self.cuda:
            batch = batch.cuda()
        with torch.no_grad():
            preds = self.net(batch)

        cls_logits, mask_logits = preds['cls_logits'][0], preds['proto'][0]
        cls_logits  = cls_logits.view(mask_logits.size(1), -1)
        cls_score   = torch.nn.Softmax(dim=1)(cls_logits) # [BG_cls+N, FG_cls+1]

        preds_score = torch.nn.Softmax(dim=1)(mask_logits)
        preds_score = F.interpolate(preds_score,
                                    size=[height, width],
                                    mode='bilinear',
                                    align_corners=True)
        preds_score = preds_score[0] # [BG_cls+N, ht, wd]

        # remove redundent channel
        _, cls_ids = cls_score.max(axis=1)
        obj_idxes  = cls_ids[self.fg_stCh:].nonzero()
        bg_probs   = preds_score[:self.fg_stCh, :, :]

        if len(obj_idxes) > 0:
            fg_probs   = preds_score[self.fg_stCh:, :, :][obj_idxes[0], :, :]
            out_probs  = torch.cat([bg_probs, fg_probs], axis=0)
            out_scores = cls_score[self.fg_stCh:, :][obj_idxes[0], :]
        else:
            out_probs, out_scores = bg_probs, cls_score[:0, :]

        # convert to numpy
        if self.cuda:
            net_mask   = preds_score.cpu().detach().numpy().argmax(axis=0)
            out_probs  = out_probs.cpu().detach().numpy()
            out_scores = out_scores.cpu().detach().numpy()
        else:
            net_mask   = preds_score.detach().numpy().argmax(axis=0)
            out_probs, out_scores = out_probs.detach().numpy(), out_scores.detach().numpy()

        return {'mask_prob': out_probs,
                'obj_class_score': out_scores,
                'fg_stCh': self.fg_stCh,
                'net-mask': net_mask}


if __name__=='__main__':
    import cv2
    import scipy.misc as smisc  # scipy in version <= 1.2.0
    from matplotlib import pyplot as plt

    bgrI   = cv2.imread('./vision/instSeg/demo/original-24-0.jpg')
    depthI = smisc.imread('./vision/instSeg/demo/depth-24-0.png', mode='P')

    model = MaskAndClassPredictor(cuda=False)
    ret   = model.step(bgrI, depthI)

    fig, ax = plt.subplots(2,2)
    ax[0,0].imshow(bgrI[..., [2,1,0]])
    ax[0,0].set_title('RGB image')
    ax[0,1].imshow(depthI, cmap='gray')
    ax[0,1].set_title('depth image')
    ax[1,0].imshow(ret['net-mask'])
    ax[1,0].set_title('net predict mask')
    ax[1,1].imshow(ret['mask_prob'].argmax(axis=0))
    ax[1,1].set_title('final mask (with cls-score)')
    plt.show()

