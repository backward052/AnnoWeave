# Generic three-model workflow

This tutorial uses a fictional warehouse inspection example. It contains no production model, class, threshold, or workflow information.

Register three local models: `package_detector` with `package,crate`, `marker_detector` with `label,seal,tag`, and `detail_classifier` with `ok,damaged,uncertain`. Build the **two-model association → crop → downstream** template and map the models to subject, candidate, and downstream roles.

Configure the association node with `subject` and `candidate` result keys. Use `center_inside` when a candidate marker should belong to the subject whose box contains its center. Use `ioa_right` for candidate containment, `ioa_left` for subject containment, or `iou` when boxes have similar scale. Keep the crop route at `all` and send each crop to the downstream classifier.

Validate with normal matches, no candidates, multiple candidates, and subjects on image borders. Inspect every node's counts and timing before saving. Add ROI, class filters, or plugin rule nodes only after the basic pipeline behaves correctly.

```text
media input
  ├─ subject detector ──(subject)──┐
  └─ candidate detector (candidate)┤
                                   v
                            spatial association
                                   v
                              subject crops
                                   v
                         downstream crop inference
                                   v
                               result preview
```
