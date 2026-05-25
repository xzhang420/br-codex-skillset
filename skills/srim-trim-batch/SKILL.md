---
name: srim-trim-batch
description: Set up and run iterative SRIM/TRIM simulations from a Windows SRIM folder using PowerShell batch automation. Use when the user wants to run TRIM.exe repeatedly across target layer widths, create per-run output folders, preserve TRANSMIT.txt/TRIMOUT.txt outputs, calculate transmitted-ion percentages, reduce or swap TRIM.DAT ion-event files, or recover/continue a SRIM/TRIM batch workflow.
---

# SRIM/TRIM Batch

## Workflow

1. Confirm the experiment before editing files. Ask the user to clarify:
   - SRIM root folder path and whether `TRIM.exe`, `TRIM.IN`, `TRIMAUTO`, and `TRIM.DAT` are there.
   - Incident ion specification: element/isotope, atomic number/mass, energy, incidence angle, number of ions, and whether `TRIM.DAT` supplies ion events.
   - Target layer to vary: layer name/index, material/compound, elements, stoichiometry, density, units, and the width values/order to run.
   - Output needs, especially whether `TRANSMIT.txt` is required for transmitted-ion percentages.
   - Whether older outputs may be deleted or only newly created batch outputs should be cleared.
2. Inspect existing `TRIM.IN`, screenshots from the TRIM setup window, and any related workbook inputs. Do not assume V2O5/tritium settings apply to new work.
3. Print the proposed TRIM inputs back to the user and wait for approval before editing or deleting anything. Include ion data, target elements, stoichiometry, density, binding/damage parameters, disk output flags, ion count, depth grid, and exactly which old outputs will be removed.
4. Back up `TRIM.IN`, `TRIMAUTO`, and any `TRIM.DAT` before changing them.
5. If the user wants fewer ion events, preserve the full `TRIM.DAT` under a descriptive name, then create a replacement `TRIM.DAT` with the original header plus the requested number of event rows. Also update the ion count in `TRIM.IN`.
6. Ensure the `Diskfiles` line in `TRIM.IN` has the `Transmit` flag set to `1` when `TRANSMIT.txt` is needed:

```text
Ranges Backscatt Transmit Sputtered Collisions Special
0      0         1        0         0          0
```

7. Write or adapt a PowerShell runner in the SRIM root. Prefer using `scripts/run_trim_series_template.ps1` as the starting point.
8. Launch the runner with `Start-Process` from the SRIM root. It should set `TRIMAUTO` to `1`, run `TRIM.exe`, wait for completion, move `.txt` outputs into a per-width folder, parse `TRANSMIT.txt` or `TDATA.sav`, append a CSV summary row, then continue to the next width.
9. Monitor the log and active processes. If SRIM shows a runtime error, stop only the runner/TRIM processes started for this batch, restore backups, inspect the edited `TRIM.IN`, fix the script, and restart fresh.
10. After the batch completes, run a post-processing step that calculates weighted transmission fractions/harvest rates, generates plots, and writes a concise email-ready summary when requested.

## PowerShell Runner Guidance

- Use PowerShell-native file operations with `-LiteralPath`.
- Before deleting or moving outputs, resolve paths and confirm they remain inside the intended `SRIM Outputs` folder.
- Keep older unrelated folders unless the user explicitly approves deleting them.
- Use batch mode by writing `TRIMAUTO` as:

```text
1

```

- Update both the target layer width in Angstrom and the plot-depth max when changing widths.
- If the user supplies widths in micrometers, convert to Angstrom with `width_um * 10000`.
- Folder names should be explicit, for example `Tritium_through_V2O5_3.5`.
- The transmitted percentage is usually `transmitted_ions / input_ions * 100`; count transmitted records from `TRANSMIT.txt` or parse an explicit transmitted-ion total if present.
- For old SRIM/TRIM GUI behavior, detect completion from a fresh `SRIM Restore\TDATA.sav` where current ions equal total ions, then close/kill the idle `TRIM.exe` so the next batch item can start.

## Approval Summary Template

Before launching a destructive rerun, show a compact approval block like:

```text
Ion: 3H, Z=1, mass=3 amu, energy=2730 keV, angle=0 deg, ions=10000
Target: Li0.33V2O5, density=3.524064795 g/cm3, solid
Stoichiometry: Li=0.045020, V=0.272851, O=0.682128
Target atoms: Li Z=3 mass=6.941; V Z=23 mass=50.9415; O Z=8 mass=15.999
Damage/binding: Li 25/3/1.63 eV; V 25/3/5.33 eV; O 28/3/2 eV
Outputs: transmit file on, other disk files off unless requested
Depth grid: ...
Will delete/replace: ...
```

Then wait for explicit user approval before deleting old output folders or launching TRIM.

## Common Checks

- `TRIM.IN` ion count matches the number of ion events in the active `TRIM.DAT`.
- The target layer row still contains the layer name and numeric width; avoid regex replacement bugs that concatenate capture references with numbers.
- `TRIM.exe` is launched with `WorkingDirectory` set to the SRIM root.
- `TRIMAUTO` is restored at the end unless the user wants batch mode left enabled.
- The summary CSV includes width, input ions, transmitted ions, percentage, output folder, and transmit file path.
- Post-run harvest calculations should document the production-rate source, thickness scaling assumption, depth weights, and any assumption that transmission is zero beyond the simulated cutoff depth.
