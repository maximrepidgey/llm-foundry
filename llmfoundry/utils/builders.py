# Copyright 2022 MosaicML LLM Foundry authors
# SPDX-License-Identifier: Apache-2.0

import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

import torch
from composer import algorithms
from composer.callbacks import (EarlyStopper, LRMonitor, MemoryMonitor,
                                OptimizerMonitor, RuntimeEstimator,
                                SpeedMonitor)
from composer.core import Evaluator
from composer.datasets.in_context_learning_evaluation import \
    get_icl_task_dataloader
from composer.loggers import (InMemoryLogger, MLFlowLogger, TensorboardLogger,
                              WandBLogger)
from composer.optim import DecoupledAdamW
from composer.optim.scheduler import (ConstantWithWarmupScheduler,
                                      CosineAnnealingWithWarmupScheduler,
                                      LinearWithWarmupScheduler)
from composer.utils import dist
from omegaconf import DictConfig, ListConfig
from omegaconf import OmegaConf as om
from transformers import AutoTokenizer, PreTrainedTokenizerBase

from llmfoundry.callbacks import (EvalGauntlet, FDiffMetrics, Generate,
                                  GlobalLRScaling, LayerFreezing,
                                  MonolithicCheckpointSaver,
                                  ScheduledGarbageCollector)
from llmfoundry.optim import (DecoupledAdaLRLion, DecoupledClipLion,
                              DecoupledLionW, DecoupledLionW_8bit)

log = logging.getLogger(__name__)


def build_icl_data_and_gauntlet(
    icl_tasks_config: Union[str, ListConfig],
    eval_gauntlet_config: Optional[Union[str, DictConfig]],
    tokenizer: PreTrainedTokenizerBase,
    device_eval_batch_size: int,
    icl_seq_len: int,
    icl_subset_num_batches: Optional[int] = None
) -> Tuple[List[Evaluator], List[str], Optional[EvalGauntlet]]:
    icl_evaluators, logger_keys = build_icl_evaluators(
        icl_tasks_config,
        tokenizer,
        icl_seq_len,
        device_eval_batch_size,
        icl_subset_num_batches=icl_subset_num_batches)
    eval_gauntlet_cb = None
    if eval_gauntlet_config is not None:
        if isinstance(eval_gauntlet_config, str):
            with open(eval_gauntlet_config, 'r') as icl_f:
                eval_gauntlet_cfg = om.load(icl_f)
            eval_gauntlet = eval_gauntlet_cfg.eval_gauntlet
        elif isinstance(eval_gauntlet_config, DictConfig):  # pyright: ignore
            eval_gauntlet = eval_gauntlet_config
        else:
            raise ValueError(
                f'Got invalid type for eval_gauntlet_config: {type(eval_gauntlet_config)}'
            )
        eval_gauntlet.logger_keys = logger_keys
        eval_gauntlet.benchmark_sizes = {
            e.label: e.dataloader.num_samples for e in icl_evaluators
        }
        eval_gauntlet_cb = EvalGauntlet(**eval_gauntlet)
    return icl_evaluators, logger_keys, eval_gauntlet_cb


def build_callback(name: str, kwargs: Dict[str, Any]):
    if name == 'lr_monitor':
        return LRMonitor()
    elif name == 'memory_monitor':
        return MemoryMonitor()
    elif name == 'speed_monitor':
        return SpeedMonitor(window_size=kwargs.get('window_size', 1),
                            gpu_flops_available=kwargs.get(
                                'gpu_flops_available', None))
    elif name == 'fdiff':
        return FDiffMetrics(**kwargs)
    elif name == 'runtime_estimator':
        return RuntimeEstimator()
    elif name == 'optimizer_monitor':
        return OptimizerMonitor(log_optimizer_metrics=kwargs.get(
            'log_optimizer_metrics', True),)
    elif name == 'generate_callback':
        prompts = kwargs.pop('prompts')
        return Generate(prompts=list(prompts), **kwargs)
    elif name == 'global_lr_scaling':
        return GlobalLRScaling(**kwargs)
    elif name == 'layer_freezing':
        return LayerFreezing(**kwargs)
    elif name == 'mono_ckpt_saver':
        return MonolithicCheckpointSaver(**kwargs)
    elif name == 'scheduled_gc':
        return ScheduledGarbageCollector(**kwargs)
    elif name == 'early_stopper':
        return EarlyStopper(**kwargs)
    else:
        raise ValueError(f'Not sure how to build callback: {name}')


def build_logger(name: str, kwargs: Dict[str, Any]):
    if name == 'wandb':
        return WandBLogger(**kwargs)
    elif name == 'tensorboard':
        return TensorboardLogger(**kwargs)
    elif name == 'mlflow':
        return MLFlowLogger(**kwargs)
    elif name == 'inmemory':
        return InMemoryLogger(**kwargs)
    else:
        raise ValueError(f'Not sure how to build logger: {name}')


def build_algorithm(name: str, kwargs: Dict[str, Any]):
    if name == 'gradient_clipping':
        return algorithms.GradientClipping(**kwargs)
    elif name == 'alibi':
        return algorithms.Alibi(**kwargs)
    elif name == 'fused_layernorm':
        return algorithms.FusedLayerNorm(**kwargs)
    elif name == 'gated_linear_units':
        return algorithms.GatedLinearUnits(**kwargs)
    elif name == 'low_precision_layernorm':
        return algorithms.LowPrecisionLayerNorm(**kwargs)
    else:
        raise ValueError(f'Not sure how to build algorithm: {name}')


def build_optimizer(model: torch.nn.Module, name: str,
                    optimizer_config: Dict[str, Any]):
    if name == 'decoupled_adamw':
        return DecoupledAdamW(model.parameters(), **optimizer_config)
    elif name == 'decoupled_lionw':
        return DecoupledLionW(model.parameters(), **optimizer_config)
    elif name == 'clip_lion':
        return DecoupledClipLion(model.parameters(), **optimizer_config)
    elif name == 'adalr_lion':
        return DecoupledAdaLRLion(model.parameters(), **optimizer_config)
    elif name == 'decoupled_lionw_8b':
        return DecoupledLionW_8bit(model.parameters(), **optimizer_config)
    else:
        raise ValueError(f'Not sure how to build optimizer: {name}')


def build_scheduler(name: str, scheduler_config: Dict[str, Any]):
    if name == 'constant_with_warmup':
        return ConstantWithWarmupScheduler(**scheduler_config)
    elif name == 'cosine_with_warmup':
        return CosineAnnealingWithWarmupScheduler(**scheduler_config)
    elif name == 'linear_decay_with_warmup':
        return LinearWithWarmupScheduler(**scheduler_config)
    else:
        raise ValueError(f'Not sure how to build scheduler: {name}')


def build_tokenizer(
        tokenizer_name: str,
        tokenizer_kwargs: Dict[str, Any]) -> PreTrainedTokenizerBase:
    os.environ['TRANSFORMERS_NO_ADVISORY_WARNINGS'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_name,
                                              **tokenizer_kwargs)

    # HuggingFace does not respect the model_max_length kwarg, and overrides it with
    # min(kwargs['model_max_length'], original_config['model_max_length']), so we
    # explicitly set it here
    tokenizer.model_max_length = tokenizer_kwargs.get(
        'model_max_length',
        int(1e30),
    )

    return tokenizer


def build_icl_evaluators(
    icl_tasks: Union[str, ListConfig],
    tokenizer: PreTrainedTokenizerBase,
    default_max_seq_len: int,
    default_batch_size: int,
    destination_dir: Optional[str] = None,
    icl_subset_num_batches: Optional[int] = None,
):
    if destination_dir is None:
        destination_dir = os.getcwd()

    evaluators = []
    logger_keys = []

    icl_tasks_list = None
    if isinstance(icl_tasks, str):
        log.info(f'Extracting ICL task config from path: {icl_tasks}')
        with open(icl_tasks, 'r') as icl_f:
            icl_task_cfg = om.load(icl_f)
        icl_tasks_list = icl_task_cfg.icl_tasks
    else:
        icl_tasks_list = icl_tasks

    def _validate_cfg(icl_cfg: DictConfig):
        assert 'label' in icl_cfg
        assert 'dataset_uri' in icl_cfg and icl_cfg.dataset_uri is not None
        assert 'icl_task_type' in icl_cfg
        assert 'num_fewshot' in icl_cfg

        if 'metric_names' not in icl_cfg:
            if icl_cfg.icl_task_type == 'language_modeling':
                icl_cfg.metric_names = ['InContextLearningLMAccuracy']
            elif icl_cfg.icl_task_type == 'multiple_choice':
                icl_cfg.metric_names = [
                    'InContextLearningMultipleChoiceAccuracy'
                ]
            elif icl_cfg.icl_task_type == 'schema':
                icl_cfg.metric_names = [
                    'InContextLearningMultipleChoiceAccuracy'
                ]
            elif icl_cfg.icl_task_type == 'question_answering':
                icl_cfg.metric_names = ['InContextLearningQAAccuracy']
            elif icl_cfg.icl_task_type == 'code_evaluation':
                icl_cfg.metric_names = ['InContextLearningCodeEvalAccuracy']
            else:
                raise ValueError(
                    f'No metric_names defined, unable to build default metrics for icl_task_type={icl_cfg.icl_task_type}.'
                )

        if 'prompt_string' not in icl_cfg:
            icl_cfg.prompt_string = ''
        if 'example_delimiter' not in icl_cfg:
            icl_cfg.example_delimiter = '\n'
        if 'continuation_delimiter' not in icl_cfg:
            icl_cfg.continuation_delimiter = ' '
        if 'max_seq_len' not in icl_cfg:
            icl_cfg.max_seq_len = default_max_seq_len
        if 'batch_size' not in icl_cfg:
            icl_cfg.batch_size = default_batch_size
        if 'pass_at_k' not in icl_cfg:
            icl_cfg.pass_at_k = 1
        if 'num_beams' not in icl_cfg:
            icl_cfg.num_beams = 20

    for icl_cfg in icl_tasks_list:
        assert isinstance(icl_cfg, DictConfig)
        _validate_cfg(icl_cfg)
        for num_fewshot in list(icl_cfg.num_fewshot):
            if tokenizer.pad_token_id is None:
                # Current workaround to support GPT2 tokenizer with `pad_token_id = None`
                pad_tok_id = tokenizer.eos_token_id
            else:
                pad_tok_id = tokenizer.pad_token_id
            label = f'{icl_cfg.label}/{num_fewshot}-shot'
            metric_names = list(icl_cfg.metric_names)
            # TODO: fix Composer bug when copying local paths and destination exists
            destination_path = f'{destination_dir}/{icl_cfg.label}-{num_fewshot}.jsonl'
            if dist.get_local_rank() == 0 and os.path.exists(destination_path):
                os.remove(destination_path)
            dist.barrier()

            dataloaders = get_icl_task_dataloader(
                icl_cfg.icl_task_type,
                icl_cfg.dataset_uri,
                tokenizer,
                batch_size=icl_cfg.batch_size,
                max_seq_len=icl_cfg.max_seq_len,
                pad_tok_id=pad_tok_id,
                num_fewshot=num_fewshot,
                prompt_string=icl_cfg.prompt_string,
                example_delimiter=icl_cfg.example_delimiter,
                continuation_delimiter=icl_cfg.continuation_delimiter,
                destination_path=destination_path,
                pass_at_k=icl_cfg.pass_at_k,
                generations_per_sample=icl_cfg.num_beams,
                has_categories=icl_cfg.get('has_categories', False),
            )
            if hasattr(
                    icl_cfg,
                    'has_categories') and icl_cfg.has_categories and isinstance(
                        dataloaders, dict):
                for category in dataloaders.keys():
                    logger_keys.extend([
                        f'metrics/{label}/{category}/{m}' for m in metric_names
                    ])
                    evaluators.append(
                        Evaluator(label=f'{label}/{category}',
                                  dataloader=dataloaders[category],
                                  metric_names=metric_names),)
            else:
                logger_keys.extend(
                    [f'metrics/{label}/{m}' for m in metric_names])
                evaluators.append(
                    Evaluator(label=label,
                              dataloader=dataloaders,
                              metric_names=metric_names,
                              subset_num_batches=icl_subset_num_batches))

    return evaluators, logger_keys
