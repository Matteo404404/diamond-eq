# Diamond EQ

Equalizes headphones and IEMs to the [PEQdB Diamond](https://doi.org/10.25740/bp189zs9642) target,
system-wide, on this PC (PipeWire) and on an Android phone that syncs its profiles from the PC.

- Pick a model from about 24,500 measured headphones and IEMs; the EQ is calculated from its measurement.
- Every output remembers its own profile. Bluetooth headphones are recognised by address and switch on
  when they connect; for the headphone jack or a USB DAC you choose what is plugged in.
- The preamp is calculated from the real peak of the curve, so no volume is given away beyond what the
  correction itself needs.

## Use

```sh
./install.sh            # installs the `diamondeq` command and the background service
diamondeq open          # web UI at http://127.0.0.1:8377
```

Everything can be done in the web UI. The same from a terminal:

```sh
diamondeq search dawn ms
diamondeq add inawaken dawn ms --output Headphones   # create a profile and use it on that output
diamondeq status                                     # outputs, profiles, what is active
diamondeq assign Headphones "moondrop"               # switch the jack to another profile ('none' removes it)
diamondeq off / on                                   # bypass / resume
diamondeq selftest                                   # measure the EQ PipeWire really applies
diamondeq import "My preset" file.txt                # use an Equalizer APO file, e.g. exported from peqdb.com
diamondeq export <profile> --format apo|graphiceq|json
diamondeq refresh                                    # update the list of measured models
```

`diamondeq add` options: `--bands N` (default 10), `--site NAME` (which reviewer's measurement),
`--all` (average all of them), `--max-loss DB`, `--exact`.

### Phone

```sh
android/tools/setup-toolchain.sh   # once: JDK 21, Android SDK, Gradle (user space)
android/build.sh                   # builds android/app/build/outputs/apk/debug/app-debug.apk
diamondeq phone --install          # install over USB (USB debugging on) and sync
```

The app applies the EQ to all sound on the phone (no root). It syncs profiles from the PC when opened:
over USB whenever the phone is plugged in, and over Wi-Fi after the first USB sync. Bluetooth headphones
get the same profile as on the PC automatically. For wired outputs (USB dongle, jack) tap the profile
button once to choose the IEM; the choice is remembered per output.

## How it works

**Target.** Diamond is the diffuse-field response of the human ear (ISO 11904-1, 47 subjects) plus a
low shelf (107 Hz, +11.8 dB, Q 0.6), a dip (2724 Hz, -3.6 dB, Q 1.2) and a high shelf (4448 Hz,
+2.7 dB, Q 0.7). The curve in `diamondeq/data/diamond_target.csv` applies to measurements made on
IEC 60318-4 ear simulators (711 couplers for IEMs, GRAS rigs for over-ears); peqdb.com uses the same
curve for both.

**Measurements.** Model lists come from the [squig.link](https://squig.link) reviewer databases and
from oratory1990's measurements as published in [AutoEq](https://github.com/jaakkopasanen/AutoEq).
Sites are ranked by the reliability order PEQdB uses. B&K 5128 databases are skipped (different rig,
different target). graph.hangout.audio does not allow data downloads and is left out.

**Correction.** Target minus the measurement, smoothed 1/12 octave up to 4 kHz widening to 2/3 octave
above 7 kHz, and faded out between 10 and 20 kHz. Narrow treble peaks and dips in coupler data move
with insertion depth and ear shape, so only their trend is corrected. With these settings the wanted
correction agrees with PEQdB's own presets within 0.2 dB RMS below 6 kHz and 0.3 dB from 6 to 10 kHz
(checked on two earphones).

**Filters.** A least-squares fit of peaking and shelving filters (default 10). The preamp puts the
highest point of the combined curve 0.2 dB below full scale at both 44.1 and 48 kHz, so the EQ never
clips and never attenuates more than needed.

**Volume loss.** Matching Diamond usually means boosting treble, and digital EQ can only boost by
turning everything else down. The profile view shows the cost ("music plays about N dB quieter").
By default a profile matches the target exactly unless that would cost more than 9 dB; then the
largest boosts are flattened until it fits. The limit can be lowered (louder, less exact in the
boosted region) or removed in the UI or with `--max-loss` / `--exact`.

**PC playback.** Each equalized output gets its own `pipewire -c` filter-chain process with exact
biquads, attached as a WirePlumber smart filter: streams going to that output pass through it, the
output stays the default device, and stopping the process removes the EQ. `diamondeq selftest`
plays periodic noise through the filter on a silent test output and compares the result with the
design (measured here: within 0.1 dB).

**Phone playback.** Without root, system-wide EQ on Android goes through the DynamicsProcessing
effect on the global output mix. That engine applies one gain per FFT bin, so the app gives each low
bin its own band and uses 1/24-octave bands above. The block length sets bass resolution and delay:
"Precise" (85 ms, the default) follows a typical profile within 0.1 dB; a very narrow bass filter
needs "Maximum" (171 ms); "Balanced" (43 ms) keeps video in sync at the cost of bass detail.
`tests/test_android_engine.py` models the engine from the AOSP source and checks these figures.
Players that bypass Android's mixer (bit-perfect USB modes, some offload paths) bypass the EQ too.

## Layout

```
diamondeq/          Python package
  curves.py         grid, target, measurement parsing and smoothing, loudness estimate
  biquad.py         filter maths
  optimizer.py      correction curve, filter fit, preamp, loss limit
  sources.py        model index and measurement download (cached in ~/.cache/diamondeq)
  profile.py        profiles (~/.local/share/diamondeq/profiles), import/export
  pipewire.py       outputs, filter-chain config, filter processes
  daemon.py         service: per-output reconcile loop, web UI/API, phone sync
  state.py          assignments and switches (~/.config/diamondeq/state.json)
  selftest.py       measures the applied EQ
  web/              UI
android/            Kotlin app (dev.diamondeq), no dependencies beyond the SDK
tests/              pytest; android_model.py mirrors the app's band layout
install.sh          command wrapper + systemd user unit
```

## Service and network

`systemctl --user status|restart|stop diamondeq`. The web UI and API only answer on localhost.
For the phone, port 8377 also listens on the LAN, but other machines can reach nothing except the
read-only profile sync, and only with the token the phone received over USB. Set `"lan": false` in
`~/.config/diamondeq/state.json` and restart the service to turn that off (USB sync keeps working).

## Tests

```sh
python3 -m pytest
```
