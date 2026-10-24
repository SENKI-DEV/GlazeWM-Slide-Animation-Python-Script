# Revolver workspace switcher for GlazeWM + YASB

A spinning revolver cylinder in the corner of your screen that shows your GlazeWM workspaces as
chambers, plus the animations that go with it: windows slide out and in when you change workspace,
and new windows grow into place.

This README covers the setup, **every path you need to change on another computer**, and **why the
`Alt+1`–`Alt+9` keybindings must be removed from GlazeWM**.

---

## 1. What is in the box

| File | What it does |
|---|---|
| `start_glaze_slide.bat` | **Launcher and window animator in one file.** Line 1 is a batch command that starts everything. The rest of the file is Python (the window open/move/resize animator). |
| `glaze_slide_hotkeys.py` | Owns `Alt+1`…`Alt+9`. Plays the slide-out / slide-in animation and tells GlazeWM to switch workspace. Also listens for clicks and scrolls from the revolver. |
| `revolver_overlay.py` | The revolver itself: a transparent, always-on-top window drawn in a screen corner. Spins, pops, plays click sounds, has a settings panel. |
| `revolver_config.json` | Settings for the revolver. Must sit **in the same folder as** `revolver_overlay.py`. Created and updated by the settings panel. |
| `config.yaml`, `styles.css` (YASB) | Your bar. Changed so the first widget group starts after the revolver, and the old `glazewm_workspaces` widget is removed. |
| `glazewm_snippet.yaml` | Optional GlazeWM rule that stops GlazeWM from tiling the overlay window. |
| `requirements.txt` | Python packages: `PySide6`, `websockets`. |

### How the pieces talk to each other

```
 keyboard  ──Alt+N──►  glaze_slide_hotkeys.py ──► GlazeWM  (websocket ws://127.0.0.1:6123)
                         │   ▲                        │
        raises the       │   │ click / scroll         │ workspace + window state
        "slide active"   │   │ (TCP 127.0.0.1:48721)  ▼
        signal           ▼   │                    revolver_overlay.py  (spins, shows state)
              start_glaze_slide.bat (animator)
              stands down while a slide is playing
```

* Alt+N → the hotkey script slides the current windows out, asks GlazeWM to switch, slides the new
  windows in.
* Clicking or scrolling the revolver sends the workspace number to the hotkey script, so those
  switches get the same slide animation. If the hotkey script is not running, the revolver falls
  back to a plain switch.
* While a slide plays, the animator is told to stand down (a Windows named event
  `GlazeWM_Slide_Active`, plus a flag file as a backup). Without this the two scripts fight over
  the same windows.

---

## 2. Requirements

* Windows 10 or 11
* [GlazeWM](https://github.com/glzr-io/glazewm) (written against v3; the IPC commands are the
  version-sensitive part, see *Troubleshooting*)
* [YASB](https://github.com/amnweb/yasb) status bar
* Python with `pythonw.exe` (the scripts were run with Python 3.8)
* The **JetBrainsMono Nerd Font** (`JetBrainsMono NFP`), used by the revolver and the bar

```
python -m pip install PySide6 websockets
```

Install into the **same Python** whose `pythonw.exe` you put in the batch file (see section 4).
The animator uses only the standard library. If pip refuses to install a current PySide6 on an old
Python (recent PySide6 needs Python 3.9 or newer), either install a newer Python and use its
`pythonw.exe` in the batch file, or let pip pick the older PySide6 release.

---

## 3. Install

1. Make a folder for the scripts, for example `C:\Users\<you>\Scripts\`, and put these four files
   in it together:
   * `start_glaze_slide.bat`
   * `glaze_slide_hotkeys.py`
   * `revolver_overlay.py`
   * `revolver_config.json`
2. Install the packages (section 2).
3. **Change the paths** in `start_glaze_slide.bat` line 1 (section 4).
4. **Remove the `Alt+1`–`Alt+9` workspace keybindings** from GlazeWM's config (section 5).
5. In your **GlazeWM config**, start the batch file when GlazeWM starts:
   ```yaml
   general:
     startup_commands: ['shell-exec cmd /c C:/Users/<you>/Scripts/start_glaze_slide.bat']
   ```
6. Optional but recommended: stop GlazeWM from tiling the overlay window.
   ```yaml
   window_rules:
     - commands: ["ignore"]
       match:
         - window_title: { equals: "RevolverOverlay" }
   ```
   (Rule key names differ between GlazeWM versions. Compare with your own config.)
7. Update your **YASB config** (section 4 table): remove the `glazewm_workspaces` widget from your
   bar and set the bar's `padding.left` so the first group starts after the revolver.
8. Test the look first without GlazeWM:
   `python revolver_overlay.py --demo`
9. Restart GlazeWM (or run the batch file by hand). The batch file waits 5 seconds, then starts the
   hotkey script, the animator and the revolver.

Config file locations (usual defaults, so check yours):

* GlazeWM: `%USERPROFILE%\.glzr\glazewm\config.yaml`
* YASB: `config.yaml` and `styles.css` in YASB's config folder under your user profile. The exact
  folder depends on the YASB version (for example `.config\yasb` or `.yasb`). It is the same folder
  you already edit your bar config in.

---

## 4. Paths and values to change on another computer

### Line 1 of `start_glaze_slide.bat`

```bat
@echo off & timeout /t 5 /nobreak >nul & start "" "<PYTHONW>" "<SCRIPTS>\glaze_slide_hotkeys.py" & start "" "<PYTHONW>" -x "%~f0" & start "" "<PYTHONW>" "<SCRIPTS>\revolver_overlay.py" & exit /b
```

| Replace | With | Currently |
|---|---|---|
| `<PYTHONW>` (3 places) | Full path to `pythonw.exe`. Run `where pythonw` in a terminal to find it. | `C:\Users\kian\AppData\Local\Programs\Python\Python38\pythonw.exe` |
| `<SCRIPTS>\glaze_slide_hotkeys.py` | Where you saved the hotkey script | `C:\Users\kian\Scripts\glaze_slide_hotkeys.py` |
| `<SCRIPTS>\revolver_overlay.py` | Where you saved the overlay | `C:\Users\kian\Scripts\revolver_overlay.py` |

> **Do not add lines to this file and do not reflow line 1.** Everything below line 1 is Python
> code. `pythonw -x` runs the file as Python and skips line 1. Use `pythonw` (no console window)
> for normal use. Use `python` temporarily if you want to see error messages.

### Other places

| Where | What to change | Currently |
|---|---|---|
| GlazeWM `config.yaml` → `general.startup_commands` | Path to the batch file | `C:/Users/kian/Scripts/start_glaze_slide.bat` |
| YASB `config.yaml` → bar `padding.left` | Width of the revolver plus the gap. The revolver prints the exact number when it starts (run it with `python` to see it), and the settings panel shows it too. | `63` |
| YASB `config.yaml` → bar `dimensions.height` and `padding` | Keep in sync with `bar_height`, `bar_margin` and `pill_height` in `revolver_config.json`. | height `36`, top padding `4` |
| `revolver_config.json` → `bar_height` | Your bar's height (`dimensions.height`) | `36` |
| `revolver_config.json` → `bar_margin` | Gap between the screen edge and the bar | `0` |
| `revolver_config.json` → `pill_height` | Height of one widget group: bar height − top/bottom padding − group margins | `24` |
| `revolver_config.json` → `screen` | Monitor index (`0` = primary) | `0` |
| `revolver_config.json` → `corner` | `top-left`, `top-right`, `bottom-left` or `bottom-right` | `top-left` |
| `revolver_config.json` → `chambers` / `workspace_names` | Number of workspaces. If yours are not named `1`…`N`, list the names, for example `["1","2","3"]`. | 6 chambers |
| `revolver_config.json` → `port` | GlazeWM IPC port (also `URI` at the top of `glaze_slide_hotkeys.py`) | `6123` |
| `revolver_config.json` → `slide_port` | Must equal `CONTROL_PORT` in `glaze_slide_hotkeys.py` | `48721` |
| `revolver_config.json` → `font` | Installed font name | `JetBrainsMono NFP` |

Things that are not paths but depend on the machine:

* **Display scaling.** Sizes are in layout pixels, so they follow Windows scaling. If the bar
  height differs from 36, change `bar_height` and `pill_height` so the focused chamber lines up
  with the bar.
* **Workspace names.** The hotkey script handles workspaces named `1` to `9` only. Other names still
  switch (without the slide animation).
* **Monitors.** The slide animation assumes one monitor.
* **User name.** Every `C:\Users\kian\...` path above contains the user name.

---

## 5. Why `Alt+1`–`Alt+9` must be removed from GlazeWM

`glaze_slide_hotkeys.py` **owns** those keys. It installs its own global keyboard hook, and when you
press `Alt+N` it swallows the key press so no other program sees it, then runs this sequence:

1. slide the current workspace's windows out,
2. tell GlazeWM to focus workspace N (over its IPC websocket),
3. put the old windows back at their real positions (GlazeWM has hidden them by now),
4. slide the new workspace's windows in.

If GlazeWM also binds `alt+1`…`alt+9`, two programs react to the same key press:

* **Order of hooks decides who wins.** Windows hands a key press to the hook installed *last*
  first. The script only works if it started *after* GlazeWM. If GlazeWM is restarted (or
  re-installs its hook) while the script keeps running, GlazeWM's hook moves to the front and sees
  the key first. `Alt+N` then switches instantly with **no animation**, and it can even happen
  during a slide.
* **A double switch fights the animation.** If GlazeWM switches at the same moment the script is
  mid slide-out, the windows jump or flash, and the script restores window positions on the
  assumption that GlazeWM has not moved anything yet. That is a common way to end up with windows
  left in the wrong place.
* **Removing the bindings makes startup order irrelevant.** The script is then the only thing that
  handles `Alt+1`…`Alt+9`.

What to remove from your GlazeWM `config.yaml`, all nine entries like this:

```yaml
keybindings:
  # REMOVE (or comment out) these:
  - commands: ["focus --workspace 1"]
    bindings: ["alt+1"]
  # ... through alt+9 ...
```

What to keep:

* **`Alt+Shift+N` (move a window to workspace N) can stay.** The script only reacts to a plain `Alt`
  press and ignores combinations that include Shift, Ctrl or the Windows key.
* Bindings for focusing windows, resizing, and so on are unaffected.

The trade-off: with the bindings removed, `Alt+1`…`Alt+9` do nothing if the hotkey script is not
running. Clicking or scrolling the revolver still works in that case (it falls back to a plain
switch), so you are never locked out.

---

## 6. Using it

| Action | Result |
|---|---|
| `Alt+1`…`Alt+9` | Slide animation + the revolver spins to that workspace |
| Left-click a chamber | Switch to that workspace (with the slide animation) |
| Mouse wheel over the revolver | Previous / next workspace |
| Hover the revolver | It grows to the pop size (can be turned off) |
| Left-click the **body** (not a chamber) | Opens the settings panel |
| Right-click | Menu with **Settings…** and **Quit revolver** |

Chamber states: **filled accent color** = focused workspace, **lighter chamber** = has windows,
**faint chamber** = empty. The long multi-rotation spin plays only when you jump at least 2
workspaces (for example 1→3). Moving to the next workspace is a single short turn.

### Settings panel

Changes apply immediately and are saved to `revolver_config.json`.

* Colors: focused accent, body, border, chambers, chamber outline, numbers
* Size (resting) and pop size
* Extra spins, how far a jump must be to get them, time per extra spin
* Sound on/off and volume, hover pop, flat bottom cut
* **Reset to defaults**

After changing the size, update YASB's `padding.left` to the number the panel shows.

---

## 7. Configuration reference

### `revolver_config.json`

| Key | Meaning | Default |
|---|---|---|
| `chambers`, `workspace_names` | Number / names of workspaces | 6, `null` (= "1".."N") |
| `corner`, `offset_x`, `offset_y`, `screen` | Where the revolver sits | `top-left`, 0, 0, 0 |
| `bar_margin`, `bar_height`, `pill_height`, `chamber_size`, `gap` | Fit to your bar | 0, 36, 24, 22, 4 |
| `base_scale` | Resting size multiplier | 1.4 |
| `pop_scale`, `pop_grow_ms`, `pop_shrink_ms` | Size while spinning / hovering and how fast it changes | 5.0, 150, 280 |
| `flat_cut` | Cut the cylinder flat at the bottom of the bar groups (`false` = full circle) | `false` |
| `spin_ms`, `extra_spins`, `long_spin_min_distance`, `spin_ms_per_extra`, `settle_overshoot_deg` | Spin behavior | 520, 2, 2, 450, 12 |
| `accent_color`, `chamber_color`, `chamber_rim_color`, `text_color`, `body_color`, `border_color` | Colors (`"#rrggbb"`, `"#aarrggbb"` or `[r,g,b,a]`) | Catppuccin-style |
| `hover_pop`, `hide_in_fullscreen` | Pop on hover; hide while a fullscreen app runs | `true`, `true` |
| `sound`, `sound_volume` | Click sounds | `true`, `0.6` |
| `port`, `slide_port` | GlazeWM IPC port; hotkey script control port | 6123, 48721 |
| `font` | Font name | `JetBrainsMono NFP` |

Restart the revolver after editing the file by hand. Changes made in the settings panel need no
restart.

### `glaze_slide_hotkeys.py` (constants at the top)

`URI` (GlazeWM IPC address), `OUT_DURATION` / `OUT_DISTANCE`, `IN_DURATION` / `IN_DISTANCE` (slide
timing and travel), `CONTROL_PORT`, `DEBUG`, `FOCUS_FORMATS` (the focus command variants it tries).

### `start_glaze_slide.bat` animator (constants near the top of the Python part)

`DURATION_MS`, `FPS`, `EASING`, `OPEN_*` (new-window animation), `RECONCILE*` (the check that fixes
windows left off target), `CONSOLE_MOVE_ONLY` (terminals move without per-frame resizing), `DEBUG`.

---

## 8. Ports, files and logs

| Item | Where |
|---|---|
| GlazeWM IPC | `ws://127.0.0.1:6123` |
| Revolver → hotkey script | TCP `127.0.0.1:48721` (local machine only) |
| Slide-active signal | Windows named event `GlazeWM_Slide_Active` and flag file `%TEMP%\glaze_slide_active.flag` |
| Animator log | `start_glaze_slide.log`, next to the batch file (only while `DEBUG = True`) |
| Revolver log | `revolver_overlay.log`, next to the script. Records errors and which sound backend is used. |
| Sound files (fallback only) | `%TEMP%\revolver_sounds\` |

Set `DEBUG = False` in the batch file once everything works. The log grows quickly and writing it
slows every animation a little.

---

## 9. Starting, stopping, restarting

* **Quit the revolver:** right-click it → *Quit revolver*.
* **Restart everything:** end the `pythonw.exe` processes in Task Manager (Details tab), then run
  the batch file again. The three scripts should always be restarted together, because the hotkey
  script should start *after* GlazeWM and the animator and hotkey script share the slide signal.
* **See errors:** run a script with `python` instead of `pythonw` from a terminal.
  `python revolver_overlay.py` for the revolver. For the hotkey script, `python glaze_slide_hotkeys.py`.

---

## 10. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| No revolver at all | The batch file does not launch it (wrong path on line 1), PySide6 is missing for that Python, or it crashed. Check `revolver_overlay.log`, then run `python revolver_overlay.py` in a terminal. |
| Revolver covers the first widgets in the bar | YASB `padding.left` is too small. Use the number the settings panel shows. |
| Focused chamber is not level with the bar text | `bar_height`, `bar_margin`, `pill_height` do not match your bar. |
| `Alt+N` switches with no animation | The `alt+1..9` GlazeWM bindings are still there, or the hotkey script is not running. See section 5. |
| `Alt+N` does nothing | The hotkey script is not running, or GlazeWM rejected the focus command. See `FOCUS_FORMATS` below. |
| Clicking the revolver switches but windows do not slide | The hotkey script is not running or started after the revolver. Restart all scripts together. Confirm `slide_port` equals `CONTROL_PORT`. |
| Windows end up in the wrong place after fast switching | Make sure you restarted all three scripts after updating. In `start_glaze_slide.log`, look for `slide flag seen via event`. If you only ever see `via file`, the event is not being shared (for example, one script is elevated). |
| An admin / elevated window does not slide | Windows blocks a normal process from moving elevated windows. Run the scripts elevated, or accept that those windows do not animate. |
| No sound, or the sound is uneven | Check the first line of `revolver_overlay.log`. It should say `sound backend: live mixer (QAudioSink)`. |
| GlazeWM "rejected every focus command format" | GlazeWM's command syntax differs. Edit `FOCUS_FORMATS` at the top of `glaze_slide_hotkeys.py` to match your version. |
| Revolver appears over fullscreen apps | `hide_in_fullscreen` is `false`, or the app is not exclusive-fullscreen. |

---

## 11. Reverting

1. Delete the revolver, hotkey-script and animator launches from line 1 of `start_glaze_slide.bat`
   (or remove the `startup_commands` entry).
2. Restore the nine `alt+1`…`alt+9` `focus --workspace N` keybindings in GlazeWM's config.
3. Put `"glazewm_workspaces"` back into your YASB group and set `padding.left` back to its old value.
