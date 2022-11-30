# Pytorch-Lightning-Auto-LR-Finder

Identifies the best learning rate for the optimizer on the specific train and val dataloaders; Automatically assigns the learning rate to before starting a training process.

### Implementation for a custom data (Dataset and Dataloader)
- get your dataset, split into train, validation (test) sets and create dataloaders;
- pass the training and validation dataloaders:
```python
lr_finder = trainer.tuner.lr_find(model, train_dataloaders=train_loader, val_dataloaders=val_loader)
```
- apply the best learning rate value to the optimizer:
```python
model.hparams.lr = lr_finder.suggestion()
```

![Image](https://user-images.githubusercontent.com/50166164/204727902-3d660f6c-4c73-441b-9161-91e0bf74d4eb.PNG)

Run using terminal
```python
python auto_lr_finder.py --batch_size=256 --device=cuda:2 
```
