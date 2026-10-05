---
bump: minor
---
### Fixed
- **Net worth history now counts property, so its last point equals `finnamon networth`.** Properties keep no value history, so every point uses the current value and says so (`property_basis`). The dashboard's chart no longer adds property a second time.
- **The dashboard's Import CSV shows a preview before saving.** Choosing a file shows the first five rows with whether each reads as spending or money in, and a "Flip signs" toggle for card exports that list purchases as positive. A file that parses to no rows shows an error instead of a success toast.
