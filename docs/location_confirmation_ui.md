# Location Confirmation UI

## Goal

The search result should not only display a guessed shelf. It should let a user
quickly confirm or correct the guess while the book, cover, and evidence are in
front of them.

## Current UI Slice

- Each search result card has a location review panel.
- The panel shows the top shelf guess, confidence, observation count, and nearby
  alternate shelf candidates.
- The user can mark the guess as correct, mark it as wrong, enter a corrected
  shelf ID, or reset the review state.
- The current implementation keeps review state in the browser only. This is a
  product/UI slice, not the persistence layer.

## Product Hypotheses

- A location guess needs a visible confidence cue and a low-friction correction
  action; otherwise users cannot tell whether the app is asking for trust or
  inspection.
- Alternate candidates should be one-tap corrections because many wrong guesses
  will likely be adjacent shelves rather than arbitrary shelves.
- The future API should store explicit user confirmations separately from
  machine observations so manual confirmation can become stronger evidence
  without hiding the original detector/OCR signal.
