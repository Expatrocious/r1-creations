# R1 Creations

Small apps ("creations") for the Rabbit R1. Open the site on a computer and scan a QR code with the R1 to install one.

- **Tasbih**: a dhikr counter. The side button or the scroll wheel counts, with 33 / 33 / 34 after salah and a free-count mode. Counts are stored only on the R1.
- **Wall St Pager**: stocks and crypto on a phosphor-green pager screen, with price charts.
- **Agent Meter**: Claude usage at a glance (5-hour session and weekly limits, reset countdowns). A service on the PC (`tools/agent-meter/`) publishes the numbers to a secret gist that the creation reads, so its install QR is personal and made on the PC with `agent-meter qr`.

Plain HTML, no build step, no server. Hosted with GitHub Pages. Each creation lives in its own folder; helper scripts are in `tools/`.
