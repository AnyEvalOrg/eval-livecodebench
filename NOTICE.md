# Notices

The eval-livecodebench package code is licensed under Apache-2.0 (LICENSE).

## Dataset

Source: https://huggingface.co/datasets/livecodebench/code_generation_lite
Revision: `0fe84c3912ea0c4d4a78037083943e8f0c4dd505`, config `release_v6`.
Problems originate from LeetCode, AtCoder and Codeforces; their original authors
retain their rights. Packaging does not relicense those problem statements or tests.

The upstream dataset card declares **`license: cc`** without specifying a Creative
Commons variant or version. Its dataset builder independently declares **MIT License**.
These declarations conflict; we preserve both and do not invent a CC variant or
assert that Apache-2.0 covers the data. Upstream clarification is needed to establish
precise redistribution terms for the dataset and original platform content.

Sources at the pinned revision:
- https://huggingface.co/datasets/livecodebench/code_generation_lite/blob/0fe84c3912ea0c4d4a78037083943e8f0c4dd505/README.md
- https://huggingface.co/datasets/livecodebench/code_generation_lite/blob/0fe84c3912ea0c4d4a78037083943e8f0c4dd505/code_generation_lite.py

## Adapted upstream code

Prompt templates, test decoding, execution prelude and comparison semantics are
adapted from LiveCodeBench/LiveCodeBench, commit
`28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`, specifically:
- `lcb_runner/prompts/code_generation.py`
- `lcb_runner/benchmarks/code_generation.py`
- `lcb_runner/evaluation/testing_util.py`

The upstream MIT notice follows (these portions remain subject to it):

MIT License

Copyright (c) 2024 LiveCodeBench

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
