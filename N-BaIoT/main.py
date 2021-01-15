import torch
import models
import torch.nn as nn
import torch.utils.data
from trainer import train_autoencoder, test_autoencoder, train_classifier, test_classifier
from print_util import Color, print_positives, print_rates
from scipy.ndimage.filters import uniform_filter1d
from data import all_devices, mirai_attacks, gafgyt_attacks, get_classifier_datasets, get_autoencoder_datasets


def compute_aggregated_predictions(predictions, ws):
    predictions_array = predictions.numpy()
    origin = (ws - 1) // 2
    result = uniform_filter1d(predictions_array, size=ws, origin=origin, mode='constant', cval=0.5)
    return result


def experiment_classifier(devices, epochs, normalization='0-mean 1-var'):
    model = models.BinaryClassifier(activation_function=torch.nn.ELU,
                                    hidden_layers=[40, 10, 5])

    criterion = nn.BCELoss()
    optimizer = torch.optim.Adadelta(model.parameters(), lr=1.0, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.5)

    dataset_train, dataset_test = get_classifier_datasets(devices, normalization)

    dataloader_train = torch.utils.data.DataLoader(dataset_train, batch_size=64, shuffle=True)
    dataloader_test = torch.utils.data.DataLoader(dataset_test, batch_size=4096)

    # Training
    print("Training")
    train_classifier(model, epochs, dataloader_train, optimizer, criterion, scheduler)

    # Testing
    tp, tn, fp, fn = test_classifier(model, dataloader_test)
    print_rates(tp, tn, fp, fn)

    return tp, tn, fp, fn


def experiment_autoencoder(devices, epochs, normalization='0-mean 1-var', ws=1):
    model = models.SimpleAutoencoder(activation_function=torch.nn.ELU,
                                     hidden_layers=[86, 58, 38, 29, 38, 58, 86])

    criterion = nn.MSELoss(reduction='none')
    optimizer = torch.optim.Adadelta(model.parameters(), lr=1.0, weight_decay=5*1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, threshold=1e-2,
                                                           factor=0.5, verbose=True)

    dataset_benign_train, dataset_benign_opt, dataset_benign_test, datasets_mirai, datasets_gafgyt = \
        get_autoencoder_datasets(devices, normalization)

    # Training
    dataloader_benign_train = torch.utils.data.DataLoader(dataset_benign_train, batch_size=64, shuffle=True)
    train_autoencoder(model, epochs, dataloader_benign_train, optimizer, criterion, scheduler)

    # Threshold computation (we use the training set but with a larger batch size to go faster)
    dataloader_benign_opt = torch.utils.data.DataLoader(dataset_benign_opt, batch_size=4096)
    losses = test_autoencoder(model, dataloader_benign_opt, criterion, '[Benign (opt)]')
    avg_loss_val = losses.mean()
    std_loss_val = losses.std()
    threshold = avg_loss_val + std_loss_val
    print('The threshold is {:.4f}\n'.format(threshold.item()))

    tp, tn, fp, fn = 0, 0, 0, 0

    # Benign validation
    dataloader_benign_test = torch.utils.data.DataLoader(dataset_benign_test, batch_size=4096)
    losses = test_autoencoder(model, dataloader_benign_test, criterion, '[Benign (test)]')
    predictions = torch.gt(losses, threshold).int()
    aggregated_predictions = torch.tensor(compute_aggregated_predictions(predictions, ws=ws))
    final_predictions = torch.gt(aggregated_predictions, 0.5).int()
    positive_predictions = final_predictions.sum().item()
    print_positives(positive_predictions, len(predictions))
    fp += positive_predictions
    tn += len(dataset_benign_opt) - positive_predictions

    # Mirai validation
    if datasets_mirai is not None:
        dataloaders_mirai = [torch.utils.data.DataLoader(dataset, batch_size=4096) for dataset in datasets_mirai]
        for i, attack in enumerate(mirai_attacks):
            losses = test_autoencoder(model, dataloaders_mirai[i], criterion, '[Mirai ' + attack + ']')
            predictions = torch.gt(losses, threshold)
            positive_predictions = predictions.int().sum().item()
            print_positives(positive_predictions, len(predictions))
            tp += positive_predictions
            fn += len(datasets_mirai[i]) - positive_predictions

    # Gafgyt validation
    for i, attack in enumerate(gafgyt_attacks):
        dataloaders_gafgyt = [torch.utils.data.DataLoader(dataset, batch_size=4096) for dataset in datasets_gafgyt]
        losses = test_autoencoder(model, dataloaders_gafgyt[i], criterion, '[Gafgyt ' + attack + ']')
        predictions = torch.gt(losses, threshold)
        positive_predictions = predictions.int().sum().item()
        print_positives(positive_predictions, len(predictions))
        tp += positive_predictions
        fn += len(datasets_gafgyt[i]) - positive_predictions

    print_rates(tp, tn, fp, fn)

    return tp, tn, fp, fn


def single_autoencoder():
    print(Color.BOLD + Color.RED + 'All devices combined' + Color.END)
    tp, tn, fp, fn = experiment_autoencoder(all_devices, epochs=0, normalization='0-mean 1-var', ws=1)
    print_rates(tp, tn, fp, fn)


def multiple_autoencoders():
    window_sizes = [1, 1, 1, 1, 1, 1, 1, 1, 1]  # window_sizes = [82, 20, 22, 65, 32, 43, 32, 23, 25]
    tp, tn, fp, fn = 0, 0, 0, 0
    for i, device in enumerate(all_devices):
        print(Color.BOLD + Color.RED + '[' + repr(i+1) + '/' + repr(len(all_devices)) + '] ' + device + Color.END)
        current_tp, current_tn, current_fp, current_fn = \
            experiment_autoencoder([device], epochs=0, normalization='0-mean 1-var', ws=window_sizes[i])
        tp += current_tp
        tn += current_tn
        fp += current_fp
        fn += current_fn
        print()
    print_rates(tp, tn, fp, fn)


def single_classifier():
    print(Color.BOLD + Color.BLUE + 'All devices combined' + Color.END)
    tp, tn, fp, fn = experiment_classifier(devices=all_devices, epochs=5, normalization='0-mean 1-var')
    print_rates(tp, tn, fp, fn)


def multiple_classifiers():
    tp, tn, fp, fn = 0, 0, 0, 0
    for i, device in enumerate(all_devices):
        print(Color.BOLD + Color.BLUE + '[' + repr(i + 1) + '/' + repr(len(all_devices)) + '] ' + device + Color.END)
        current_tp, current_tn, current_fp, current_fn = experiment_classifier(devices=[device], epochs=2,
                                                                               normalization='0-mean 1-var')
        tp += current_tp
        tn += current_tn
        fp += current_fp
        fn += current_fn
        print()
    print_rates(tp, tn, fp, fn)


def main():
    multiple_autoencoders()

# TODO: other models (for example autoencoder + reconstruction of next sample, multi-class classifier)
#  => the objective is to have a greater variety of results

# TODO: federated learning simulation


if __name__ == "__main__":
    main()
