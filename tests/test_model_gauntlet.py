from typing import Dict, List
from composer.loggers import Logger, InMemoryLogger
import pytest
import os
import omegaconf as om
from transformers import AutoTokenizer
from llmfoundry.utils.builders import build_icl_data_and_gauntlet
from composer.metrics import InContextLearningLMAccuracy
import torch
from composer.core import State

@pytest.fixture(autouse=True)
def set_correct_cwd():
    if not os.getcwd().endswith('llm-foundry/scripts'):
        os.chdir('scripts')

    yield

    if os.getcwd().endswith('llm-foundry/scripts'):
        os.chdir('..')

class MockState(State):
    def __init__(self, logger_keys: List[str], accuracy: float=0.25) -> None:
        self.eval_metrics = {}
        self.timestamp = 0
        for key in logger_keys:
            dl_name = '/'.join(key.split('/')[1:-1])
            self.eval_metrics[dl_name] = {}
            self.eval_metrics[dl_name]['InContextLearningLMAccuracy'] = InContextLearningLMAccuracy()
            self.eval_metrics[dl_name]['InContextLearningLMAccuracy'].correct = torch.tensor(accuracy * 100)
            self.eval_metrics[dl_name]['InContextLearningLMAccuracy'].total = torch.tensor(100)

class MockLogger(Logger):
    def __init__(self, state: MockState):
        self.inmemorylogger = InMemoryLogger()
        self.inmemorylogger.state = state

    def log_metrics(self, metrics: Dict[str, float]) -> None: 
       self.inmemorylogger.log_metrics(metrics)

@pytest.mark.parametrize(
    'tasks_from_path', [True, False],
)
@pytest.mark.parametrize(
    'gauntlet_from_path', [True, False],
)
def test_gauntlet_callback(tasks_from_path: bool, gauntlet_from_path: bool):

    if tasks_from_path:
        icl_task_config = 'eval/yamls/lm_tasks.yaml'
    else:
        icl_task_config = om.OmegaConf.create(
            """
            - label: jeopardy
              dataset_uri: eval/local_data/world_knowledge/jeopardy_all.jsonl # ADD YOUR OWN DATASET URI
              num_fewshot: [10]
              icl_task_type: language_modeling
              continuation_delimiter: "\nAnswer: " # this separates questions from answers
              has_categories: true
            - label: bigbench_qa_wikidata
              dataset_uri: eval/local_data/world_knowledge/bigbench_qa_wikidata.jsonl # ADD YOUR OWN DATASET URI
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: lambada_openai
              dataset_uri: eval/local_data/language_understanding/lambada_openai.jsonl
              num_fewshot: [0]
              icl_task_type: language_modeling
            - label: bigbench_conlang_translation
              dataset_uri: eval/local_data/language_understanding/bigbench_conlang_translation.jsonl
              num_fewshot: [0]
              icl_task_type: language_modeling
            - label: bigbench_dyck_languages
              dataset_uri: eval/local_data/symbolic_problem_solving/bigbench_dyck_languages.jsonl
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: bigbench_cs_algorithms
              dataset_uri: eval/local_data/symbolic_problem_solving/bigbench_cs_algorithms.jsonl
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: bigbench_operators
              dataset_uri: eval/local_data/symbolic_problem_solving/bigbench_operators.jsonl
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: bigbench_repeat_copy_logic
              dataset_uri: eval/local_data/symbolic_problem_solving/bigbench_repeat_copy_logic.jsonl
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: simple_arithmetic_nospaces
              dataset_uri: eval/local_data/symbolic_problem_solving/simple_arithmetic_nospaces.jsonl
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: simple_arithmetic_withspaces
              dataset_uri: eval/local_data/symbolic_problem_solving/simple_arithmetic_withspaces.jsonl
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: pubmed_qa_labeled
              dataset_uri: eval/local_data/reading_comprehension/pubmed_qa_labeled.jsonl # ADD YOUR OWN DATASET URI
              num_fewshot: [10]
              icl_task_type: language_modeling
            - label: squad
              dataset_uri: eval/local_data/reading_comprehension/squad.jsonl # ADD YOUR OWN DATASET URI
              num_fewshot: [10]
              icl_task_type: language_modeling
            """
        )
    assert isinstance(icl_task_config, om.ListConfig) or isinstance(icl_task_config, str)

    if gauntlet_from_path:
        model_gauntlet_config = 'eval/yamls/model_gauntlet.yaml'
    else:
        model_gauntlet_config = om.OmegaConf.create(
          """
                weighting: EQUAL
                subtract_random_baseline: true
                rescale_accuracy: true
                categories:
                - name: world_knowledge
                  benchmarks:
                    - name: jeopardy
                      num_fewshot: 10
                      random_baseline: 0
                    - name: bigbench_qa_wikidata
                      num_fewshot: 10
                      random_baseline: 0
                    - name: arc_easy
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: arc_challenge
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: mmlu
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: bigbench_misconceptions
                      num_fewshot: 10
                      random_baseline: 0.5
                - name: commonsense_reasoning
                  benchmarks:
                    - name: copa
                      num_fewshot: 0
                      random_baseline: 0.5
                    - name: piqa
                      num_fewshot: 10
                      random_baseline: 0.5
                    - name: openbook_qa
                      num_fewshot: 0
                      random_baseline: 0.25
                    - name: bigbench_novel_concepts
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: bigbench_strange_stories
                      num_fewshot: 10
                      random_baseline: 0.5
                    - name: bigbench_strategy_qa
                      num_fewshot: 10
                      random_baseline: 0.5
                - name: language_understanding
                  benchmarks:
                    - name: lambada_openai
                      num_fewshot: 0
                      random_baseline: 0.0
                    - name: hellaswag
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: winograd
                      num_fewshot: 0
                      random_baseline: 0.5
                    - name: winogrande
                      num_fewshot: 0
                      random_baseline: 0.5
                    - name: bigbench_conlang_translation
                      num_fewshot: 0
                      random_baseline: 0.0
                    - name: bigbench_language_identification
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: bigbench_conceptual_combinations
                      num_fewshot: 10
                      random_baseline: 0.25
                - name: symbolic_problem_solving
                  benchmarks:
                    - name: bigbench_elementary_math_qa
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: bigbench_dyck_languages
                      num_fewshot: 10
                      random_baseline: 0
                    - name: bigbench_cs_algorithms
                      num_fewshot: 10
                      random_baseline: 0
                    - name: bigbench_logical_deduction
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: bigbench_operators
                      num_fewshot: 10
                      random_baseline: 0.0
                    - name: bigbench_repeat_copy_logic
                      num_fewshot: 10
                      random_baseline: 0.0
                    - name: simple_arithmetic_withspaces
                      num_fewshot: 10
                      random_baseline: 0.0
                    - name: simple_arithmetic_nospaces
                      num_fewshot: 10
                      random_baseline: 0.0
                    - name: math_qa
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: logi_qa
                      num_fewshot: 10
                      random_baseline: 0.25
                - name: reading_comprehension
                  benchmarks:
                    - name: pubmed_qa_labeled
                      num_fewshot: 10
                      random_baseline: 0.0
                    - name: squad
                      num_fewshot: 10
                      random_baseline: 0
                    - name: bigbench_understanding_fables
                      num_fewshot: 10
                      random_baseline: 0.25
                    - name: boolq
                      num_fewshot: 10
                      random_baseline: 0.5
          """
        )
    assert isinstance(model_gauntlet_config, om.DictConfig) or isinstance(model_gauntlet_config, str)
    tokenizer =  AutoTokenizer.from_pretrained('EleutherAI/gpt-neox-20b')

    # test loading functionality
    _, _, model_gauntlet_callback = build_icl_data_and_gauntlet(
            icl_task_config,
            model_gauntlet_config,
            tokenizer,
            4,
            1024,
            1
    )
    assert model_gauntlet_callback is not None
    state = MockState(model_gauntlet_callback.logger_keys)
    logger = MockLogger(state)

    # test computing functionality
    result = model_gauntlet_callback.eval_after_all(state, logger)

    for category in [
        'world_knowledge', 'language_understanding', 'reading_comprehension', 'symbolic_problem_solving'
    ]:
        name = f"icl/metrics/model_gauntlet/{category}"
        assert result[name] == pytest.approx(0.25)
    
    assert result['icl/metrics/model_gauntlet/average'] == pytest.approx(0.25)
