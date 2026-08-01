# Judging checklist

## Pipeline Architecture — 25%

- [ ] Every tool call has a reason for its position in the pipeline.
- [ ] The agent can recover from timeout, empty result, and malformed input.
- [ ] Architecture fits on one diagram and can be explained in 30 seconds.

## Real-time Adaptability — 25%

- [ ] Three unseen inputs complete without code changes.
- [ ] Surprise Task input is accepted through the same demo entry point.
- [ ] The team knows which parameters can be safely adjusted live.

## Prompt Quality — 20%

- [ ] Instructions define the goal, constraints, tool policy, and stop condition.
- [ ] Prompt changes are linked to eval evidence.
- [ ] Ambiguity and unsafe/unsupported requests have explicit behavior.

## Impact / Idea — 20%

- [ ] Target user and pain are concrete.
- [ ] The agent saves measurable time or enables a new outcome.
- [ ] The live result makes the value obvious without narration.

## Presentation Clarity — 10%

- [ ] Slides are at most 5 pages and fit in 2 minutes.
- [ ] Live demo fits in 3 minutes with Raw API Stream visible.
- [ ] Each teammate can answer architecture and failure-mode questions.

