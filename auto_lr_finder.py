import os, argparse, yaml, torch, torchvision, timm, pickle, wandb
from datetime import datetime
import pytorch_lightning as pl
from torch.nn import *
import torch.utils.data as data
from pytorch_lightning.callbacks import LearningRateMonitor, ModelCheckpoint
from torchvision import transforms
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import *
from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from pytorch_lightning.loggers import WandbLogger
from collections import OrderedDict as OD
from collections import namedtuple as NT
from softdataset import TripletImageDataset, data_split
from tqdm import tqdm
import torchvision.transforms.functional as TF

def run(args):
    
    model_dict = {}
    sp = args.save_path
    bs = args.batch_size
    expdir = args.expdir
    device = args.device
    path = args.ims_path
    inp_size = args.input_size
    model_name=args.model_name
    optimizer_name=args.optimizer_name
    lr = args.learning_rate
    only_features = args.only_feature_embeddings
    only_labels = args.only_target_labels
    
    
    argstr = yaml.dump(args.__dict__, default_flow_style=False)
    print(f"\nTraining Arguments:\n{argstr}\n")
    
    optimizer_hparams={"lr": lr}
    model_dict[model_name] = 0 
    os.system('wandb login 3204eaa1400fed115e40f43c7c6a5d62a0867ed1')
    
    angles=[-30, -15, 0, 15, 30] # rotation 각도, 이중 하나의 각도로 돌아감 [-30, -15, 0, 15, 30]
    distortion_scale = 0.5 # distortion 각도 (0.5)
    p = 0.5 # distortion이 적용될 확률 (0.5)
    fill = [255,255,255] # distortion 후 채워지는 배경색

    transformations = {}   

    transformations['qry'] = transforms.Compose([
                            transforms.RandomHorizontalFlip(),
                            transforms.RandomAffine(0, (0.5, 0), fill = fill),
                            transforms.RandomRotation(90, fill = fill),
                            transforms.ColorJitter([0.3, 1]),
                            transforms.GaussianBlur(9, (0.5, 3.0)),
                            transforms.ToTensor(),
                                                  ])

    transformations['pos'] = transforms.Compose([
        transforms.RandomRotation(90, fill = fill),
        transforms.RandomPerspective(distortion_scale = distortion_scale, p = p, fill = fill),
        transforms.ToTensor(),
    ])  
    transformations['neg'] = transforms.Compose([
        transforms.RandomRotation(90, fill = fill),
        transforms.RandomPerspective(distortion_scale = distortion_scale, p = p, fill = fill),
        transforms.ToTensor(),
    ])
      
    
    out_path = f"/home/ubuntu/workspace/bekhzod/triplet-loss-pytorch/pytorch_lightning/data/test_lr_dataset.json"
       
    tr_ds = TripletImageDataset(data_dir = path, transform_dic=transformations, random=True, trainval_json=out_path, trainval='train', load_images=True)
    val_ds = TripletImageDataset(data_dir = path, transform_dic=transformations, random=True, trainval_json=out_path, trainval='val', load_images=True)
    test_ds = TripletImageDataset(data_dir = path, transform_dic=transformations, random=True, trainval_json=out_path, trainval='test', load_images=True)
    
    milestones = [i*5 for i in range(1, 15)]
    gamma = 0.01
    wandb_logger = WandbLogger(name=f'lr={lr}-gamma={gamma}-{milestones}',project='Test LR')
    num_classes = tr_ds.get_prod_length()
    print(f"The dataset has {num_classes} classes")
    
    print(f"Number of train set images: {len(tr_ds)}")
    print(f"Number of validation set images: {len(val_ds)}")
    print(f"Number of test set images: {len(test_ds)}")
    
    cos = CosineSimilarity(dim=1, eps=1e-6)
    train_loader = DataLoader(tr_ds, batch_size=bs, shuffle=True, drop_last=True, num_workers=8)
    val_loader = DataLoader(val_ds, batch_size=bs, shuffle=True, drop_last=True, num_workers=8)
    test_loader = DataLoader(test_ds, batch_size=bs, shuffle=True, drop_last=True, num_workers=8)  
    labels = {"pos": torch.tensor(1.).unsqueeze(0),
              "neg": torch.tensor(-1.).unsqueeze(0)}
    
    alpha = 1
    eps = 5
    def cos_sim_score(score, eps, alpha, mode):
        # if score > 0.5:
        if mode == "for_pos":
            if score < 0.3:
                return (score + eps) / (eps + eps*alpha)
            else:
                return (score + eps) / (eps + alpha)
        # elif score > 0.5:
        elif mode == "for_neg":
            return (score + (alpha / eps)) / (2*eps)
    
    assert only_features or only_labels, "Please choose at least one loss function to train the model (triplet loss or crossentropy loss)"
    if only_features and only_labels:
        print("\nTrain using triplet loss and crossentropy loss\n")
    elif only_features == True and only_labels == None:
        print("\nTrain using only triplet loss\n")                
    elif only_features == None and only_labels == True:
        print("\nTrain using only crossentropy loss\n")      
    
    class Model(pl.LightningModule):

        def __init__(self, model_name,  optimizer_name, optimizer_hparams):
            """
            Gets model name, optimizer name and hparams and returns trained model (pytorch lightning) with results (dict).
            
            Arguments:
                model_name - Name of the model/CNN to run. Used for creating the model (see function below)
                optimizer_name - Name of the optimizer to use. Currently supported: Adam, SGD
                optimizer_hparams - Hyperparameters for the optimizer, as dictionary. This includes learning rate, weight decay, etc.
            """
            super().__init__()
            # Exports the hyperparameters to a YAML file, and create "self.hparams" namespace
            self.save_hyperparameters()

            # Create model
            self.model = create_model(model_name)
            # Create loss module
            self.cos_loss = CosineEmbeddingLoss(margin=0.5)
            self.ce_loss = CrossEntropyLoss()
            # Example input for visualizing the graph in Tensorboard
            self.example_input_array = torch.zeros((1, 3, 224, 224), dtype=torch.float32)
            if self.hparams.optimizer_hparams['lr'] is not None:
                self.hparams["lr"] = self.hparams.optimizer_hparams["lr"]
                          

        def forward(self, inp):
            
            # Function to convert dictionary to namedtuple
            def dict_to_namedtuple(dic):
                return NT('GenericDict', dic.keys())(**dic)
            
            dic = {}                        
            fm = self.model.forward_features(inp)
            pool = AvgPool2d((fm.shape[2],fm.shape[3]))
            lbl = self.model.forward_head(fm)
            dic["feature_map"] = torch.reshape(pool(fm), (-1, fm.shape[1]))
            dic["class_pred"] = lbl
            out = dict_to_namedtuple(dic)
            
            return out
        
        def configure_optimizers(self):
            if self.hparams.optimizer_name == "Adam":
                # AdamW is Adam with a correct implementation of weight decay (see here
                # for details: https://arxiv.org/pdf/1711.05101.pdf)
                optimizer = torch.optim.AdamW(self.parameters(), self.hparams.lr)
            elif self.hparams.optimizer_name == "SGD":
                optimizer = torch.optim.SGD(self.parameters(), **self.hparams.optimizer_hparams)
            else:
                assert False, f'Unknown optimizer: "{self.hparams.optimizer_name}"'
            
            scheduler = MultiStepLR(optimizer=optimizer, milestones=milestones, gamma=gamma, verbose=True)
        
            return [optimizer], [scheduler]
        
        def training_step(self, batch, batch_idx): # triplet loss 
            # "batch" is the output of the training data loader.
            
            cos_sims = []
            ims, poss, negs, clss, regs = batch['qry'], batch['pos'][0], batch['neg'][0], batch['cat_idx'], batch['prod_idx']
            
            # Get feature maps and pred labels
            out_ims = self(ims) 
            fm_ims, lbl_ims = out_ims[0], out_ims[1] # get feature maps [0] and predicted labels [1]
            out_poss = self(poss)
            fm_poss, lbl_poss = out_poss[0], out_poss[1] # get feature maps [0] and predicted labels [1]
            out_negs = self(negs)
            fm_negs, lbl_negs = out_negs[0], out_negs[1] # get feature maps [0] and predicted labels [1]
            
            # Compute loss
            if only_features and only_labels:
                loss_cos = self.cos_loss(fm_ims, fm_poss, labels["pos"]) + self.cos_loss(fm_ims, fm_negs, labels["neg"])
                loss_ce = self.ce_loss(lbl_ims, regs) + self.ce_loss(lbl_poss, regs)
                loss = loss_cos + loss_ce 
            elif only_features == True and only_labels == None:
                loss_cos = self.cos_loss(fm_ims, fm_poss, labels["pos"]) + self.cos_loss(fm_ims, fm_negs, labels["neg"])
                loss = loss_cos                 
            elif only_features == None and only_labels == True:
                loss_ce = self.ce_loss(lbl_ims, regs) + self.ce_loss(lbl_poss, regs)
                loss = loss_ce 
                
            # Compute top3 and top1
            top3, top1 = 0, 0            
            for idx, fm in (enumerate(fm_ims)):
                sim = cos(fm_ims[idx].unsqueeze(0), fm_poss[idx]) 
#                 print(fm_poss[idx].shape)
#                 print(fm_ims[idx].shape)
                cos_sims.append(sim)
                vals, inds = torch.topk(lbl_ims[idx], k=3)
                if regs[idx] in inds:
                    top3 += 1
                if regs[idx] in inds[0]:
                    top1 += 1

            return OD([('loss', loss)]) #, ('train_top3', top3 / len(ims)), ('train_top1', top1 / len(ims))])  # Return tensor to call ".backward" on

        def validation_step(self, batch, batch_idx): # triplet loss 

            cos_sims = []
            ims, poss, negs, clss, regs = batch['qry'], batch['pos'][0], batch['neg'][0], batch['cat_idx'], batch['prod_idx']

            # Get feature maps and pred labels
            out_ims = self(ims)
            fm_ims, lbl_ims = out_ims[0], out_ims[1] # get feature maps [0] and predicted labels [1]
            out_poss = self(poss)
            fm_poss, lbl_poss = out_poss[0], out_poss[1] # get feature maps [0] and predicted labels [1]
            out_negs = self(negs)
            fm_negs, lbl_negs = out_negs[0], out_negs[1] # get feature maps [0] and predicted labels [1]
            
            # Compute loss
            if only_features and only_labels:                
                loss_cos = self.cos_loss(fm_ims, fm_poss, labels["pos"]) + self.cos_loss(fm_ims, fm_negs, labels["neg"])
                loss_ce = self.ce_loss(lbl_ims, regs) + self.ce_loss(lbl_poss, regs)
                print(f"Triplet loss: {loss_cos:.3f}")
                print(f"Crossentropy loss: {loss_ce:.3f}")
                loss = loss_cos + loss_ce 
            elif only_features == True and only_labels == None:             
                loss_cos = self.cos_loss(fm_ims, fm_poss, labels["pos"]) + self.cos_loss(fm_ims, fm_negs, labels["neg"])
                loss = loss_cos
                print(f"Loss: {loss_cos:.3f}")
            elif only_features == None and only_labels == True:
                loss_ce = self.ce_loss(lbl_ims, regs) + self.ce_loss(lbl_poss, regs)
                print(f"Loss: {loss_ce:.3f}")
                loss = loss_ce                
            
            # Compute top3 and top1            
            top3, top1 = 0, 0
            
            for idx, fm in (enumerate(fm_ims)):
                sim = cos(fm_ims[idx].unsqueeze(0), fm_poss[idx]) 
                cos_sims.append(sim)
                vals, inds = torch.topk(lbl_ims[idx], k=3)
                if regs[idx] in inds:
                    top3 += 1
                if regs[idx] in inds[0]:
                    top1 += 1

            # Logs the loss per epoch to tensorboard (weighted average over batches)
            self.log("val_loss", loss)
            self.log("cos_sims", cos_sim_score(torch.mean(torch.FloatTensor(cos_sims)).item(), eps, alpha, mode='for_pos'))
            self.log("val_top3", top3 / len(fm_ims))
            self.log("val_top1", top1 / len(fm_ims))

            return OD([('loss', loss), ('val_top3', top3),
                       ('cos_sims', torch.mean(torch.FloatTensor(cos_sims)))])

        def test_step(self, batch, batch_idx): # triplet loss 
            
            """ 
            Compares test images in the batch with the all images in the dataloader.
            
            """
            fms_ims, fms_poss, fms_negs, scores, gts_all, im_pred_lbls_all, pos_pred_lbls_all = [], [], [], [], [], [], []
            top1, top3, = 0, 0
            
            print("\nObtaining embeddings and predicting labels...\n")
            # Get feature maps and pred labels of the whole test data
            for i, batch_all in enumerate(test_loader):
                with torch.no_grad():
                    
                    ims_all, poss_all, negs_all, clss_all, regs_all = batch_all['qry'], batch_all['pos'][0], batch_all['neg'][0], batch_all['cat_idx'], batch_all['prod_idx']
                    out_ims_all = self(ims_all.cuda())
                    fm_ims_all, lbl_ims_all = out_ims_all[0], out_ims_all[1] # get feature maps [0] and predicted labels [1]
                    out_poss_all = self(poss_all.cuda())
                    fm_poss_all, lbl_poss_all = out_poss_all[0], out_poss_all[1] # get feature maps [0] and predicted labels [1]
                    out_negs_all = self(negs_all.cuda())
                    fm_negs_all, lbl_negs_all = out_negs_all[0], out_negs_all[1] # get feature maps [0] and predicted labels [1]
                    fms_ims.extend(fm_ims_all)
                    fms_poss.extend(fm_poss_all)
                    fms_negs.extend(fm_negs_all)
                    gts_all.extend(regs_all.cuda())
                    im_pred_lbls_all.extend(lbl_ims_all)
                    pos_pred_lbls_all.extend(lbl_poss_all)
                
            fms_ims = torch.stack(fms_ims)
            fms_poss = torch.stack(fms_poss)
            fms_negs = torch.stack(fms_negs)
            gts_all = torch.stack(gts_all)
            im_pred_lbls_all = torch.stack(im_pred_lbls_all)
            pos_pred_lbls_all = torch.stack(pos_pred_lbls_all)
            
            print("Done!\n")               
            print("\nCalculating metrics...")
        
            # Compute loss, top3, and top1   
            for index, fm in enumerate(fms_ims):
                
                # Compute loss
                if only_features and only_labels:
                    loss_cos = self.cos_loss(fm.unsqueeze(0), fms_poss[index].unsqueeze(0), labels["pos"].to("cuda")) + self.cos_loss(fm.unsqueeze(0), fms_negs[index].unsqueeze(0), labels["neg"].to("cuda"))
                    loss_ce = self.ce_loss(im_pred_lbls_all[index], gts_all[index]) + self.ce_loss(pos_pred_lbls_all[index], gts_all[index])
                    loss = loss_cos + loss_ce 
                elif only_features == True and only_labels == None:
                    loss_cos = self.cos_loss(fm.unsqueeze(0), fms_poss[index].unsqueeze(0), labels["pos"].to("cuda")) + self.cos_loss(fm.unsqueeze(0), fms_negs[index].unsqueeze(0), labels["neg"].to("cuda"))
                    loss = loss_cos                 
                elif only_features == None and only_labels == True:
                    loss_ce = self.ce_loss(im_pred_lbls_all[index], gts_all[index]) + self.ce_loss(pos_pred_lbls_all[index], gts_all[index])
                    loss = loss_ce 
                
                score = cos(fm.unsqueeze(0), fms_poss[index].unsqueeze(0)) # (1, fm), (len(dl), fm) = (len(dl), fm)
                scores.append(score)
                vals, inds = torch.topk(im_pred_lbls_all[index], k=3)
                if gts_all[index] in inds:
                    top3 += 1
                if gts_all[index] in inds[0]:
                    top1 += 1                    

            print("Calculating metrics is done!\n")
            print('top1', top1/len(fms_ims))
            print('top3', top3/len(fms_ims))
            print('test_loss', loss / len(fms_ims))

            self.log("test_sim_scores", cos_sim_score(torch.mean(torch.FloatTensor(scores)).item(), eps, alpha, mode='for_pos'))
            self.log("test_loss", loss / len(fms_ims))
            self.log("test_top3", top3 / len(fms_ims))
            self.log("test_top1", top1 / len(fms_ims))

    def create_model(model_name, conv_input=False, num_classes=num_classes):
        
        """ 
        
        Gets model name and creates a timm model.
        
        """

        if model_name in model_dict:
            base_model = timm.create_model(model_name, pretrained=True, num_classes=num_classes)
            print(f"Model {model_name} with the best weights is successfully loaded!")        
            if conv_input:                
                conv_layer = Sequential(Conv2d(3, 3, kernel_size=(3, 3), stride=(1, 1),padding=(1,1), bias=False), 
                 SiLU(inplace=True))
                model = Sequential(conv_layer, base_model)  
            else:
                model = base_model
        else:
            assert False, f'Unknown model name "{model_name}". Available models are: {str(model_dict.keys())}'
            
        return model

    def train_model(model_name, save_name=None, **kwargs):
        
        """
        Trains the model and returns trained model with its results.
        
        Arguments:
            model_name - Name of the model you want to run. Is used to look up the class in "model_dict"
            save_name (optional) - If specified, this name will be used for creating the checkpoint and logging directory.
        """
        if save_name is None:
            save_name = model_name

        # Create a PyTorch Lightning trainer with the generation callback
        trainer = pl.Trainer(
            default_root_dir=os.path.join(sp, save_name),  # dir name to save models
            # precision=16, amp_backend='native',
            # total num of epochs
            max_epochs=300,
            log_every_n_steps=15,
            logger=wandb_logger,
            auto_lr_find=True,
#             fast_dev_run=True,
            # strategy="ddp", accelerator="gpu", devices=3,
            callbacks=[
                
                ModelCheckpoint(
                    filename='{epoch}-{val_loss:.2f}-{cos_sims:.2f}-{val_top1:.2f}', 
                    every_n_train_steps = None, save_top_k=1,
                    save_weights_only=True, mode="max", monitor="val_top1" 
                ),  # Save the best checkpoint based on the min val_loss recorded. Saves only weights and not optimizer
                EarlyStopping(monitor="val_top1", mode="max", patience=10, verbose=True), # set the metric (and change the mode!) to track for early stopping
                LearningRateMonitor("epoch"), # Log learning rate every epoch
            ]
        )
        trainer.logger._log_graph = True  # If True, we plot the computation graph in tensorboard
        trainer.logger._default_hp_metric = None  # Optional logging argument that we don't need

        # Check whether pretrained model exists. If yes, load it and skip training
        pretrained_filename = os.path.join(sp, 'models', '.ckpt')
        if os.path.isfile(pretrained_filename):
            print(f"Found pretrained model at {pretrained_filename}, loading...")
            # Automatically loads the model with the saved hyperparameters
            model = Model.load_from_checkpoint(pretrained_filename)
        else:
            pl.seed_everything(42)  # To be reproducable
            model = Model(model_name=model_name,  optimizer_name=optimizer_name, optimizer_hparams=optimizer_hparams)
            lr_finder = trainer.tuner.lr_find(model, train_dataloaders=train_loader, val_dataloaders=val_loader)
            model.hparams.lr = lr_finder.suggestion()
            trainer.fit(model, train_loader, val_loader)

        # Test best model on validation and test set
        test_result = trainer.test(model, dataloaders=test_loader, verbose=True)

        result = {"test_loss": test_result[0]["test_loss"], 
                  "test_scores": test_result[0]["test_sim_scores"],
                  "test_top3": test_result[0]["test_top3"],
                  "test_top1": test_result[0]["test_top1"]}

        return model, result    
    
    trained_model, results = train_model(
    model_name=model_name, optimizer_name=optimizer_name, save_name=f"{model_name}_{optimizer_name}_{lr}",
    optimizer_hparams=optimizer_hparams)
    test_loss = results['test_loss']
    test_top1 = results['test_top1']
    test_top3 = results['test_top3']
    with open(f"results/{model_name}_{optimizer_name}_{lr}_{test_loss}_{test_top1}_{test_top3}_results.pickle", 'wb') as handle:
                    pickle.dump(results, handle, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Results of the training are saved in results/{model_name}_{optimizer_name}_{lr}_{test_loss}_{test_top1}_{test_top3}_results.pickle")   

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description='Triplet Loss PyTorch Lightning Arguments')
    parser.add_argument('-ed', '--expdir', default=None, help='Experiment directory')
    parser.add_argument("-sp", "--save_path", type=str, default='saved_models', help="Path to save trained models")
    parser.add_argument("-bs", "--batch_size", type=int, default=64, help="Batch size")
    parser.add_argument("-d", "--device", type=str, default='cuda:1', help="GPU device number")
    parser.add_argument("-ip", "--ims_path", type=str, default='/home/ubuntu/workspace/dataset/test_dataset_svg/test_dataset_svg_1122_3_org', help="Path to the images")
#     parser.add_argument("-ip", "--ims_path", type=str, default='/home/ubuntu/workspace/dataset/test_dataset_svg/test_dataset_svg_1124_images_worker', help="Path to the images")
    # parser.add_argument("-ip", "--ims_path", type=str, default='/mnt/test_dataset_svg/test_dataset_svg_1124_images_worker', help="Path to the images")
    parser.add_argument("-is", "--input_size", type=int, default=(224, 224), help="Size of the images")
    parser.add_argument("-mn", "--model_name", type=str, default='rexnet_150', help="Model name (from timm library (ex. darknet53, ig_resnext101_32x32d))")
    parser.add_argument("-on", "--optimizer_name", type=str, default='Adam', help="Optimizer name (Adam or SGD)")
    parser.add_argument("-lr", "--learning_rate", type=float, default=1e-1, help="Learning rate value")
    parser.add_argument("-wd", "--weight_decay", type=float, default=1e-5, help="Weight decay value")
    parser.add_argument("-ofm", "--only_feature_embeddings", type=bool, default=True,
                        help="If True trains the model using only triplet loss and and return feature embeddings (if both otl and ofm are True uses two loss functions simultaneously)")
    parser.add_argument("-otl", "--only_target_labels", type=bool, default=True,
                        help="If True trains the model using only cross entropy and and return predicted labels (if both otl and ofm are True uses two loss functions simultaneously)")
    
    args = parser.parse_args() 
    
    run(args) 