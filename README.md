# Pytorch-Lightning-Auto-LR-Finder

Identifies the best learning rate for the optimizer on the specific train and val dataloaders; Automatically assigns the learning rate to before starting a training process.

### Implementation of a custom data (Dataset and Dataloader)
- get your dataset, split into train, validation (test) sets and create dataloaders;
- pass the training and validation dataloaders:
```python
lr_finder = trainer.tuner.lr_find(model, train_dataloaders=train_loader, val_dataloaders=val_loader)
```
- apply the best lr to the optimizer:
```python
model.hparams.lr = lr_finder.suggestion()
```


![Image](https://user-images.githubusercontent.com/50166164/202942637-e3674ee5-56ae-4ffb-830d-d5e42fb91072.PNG)
