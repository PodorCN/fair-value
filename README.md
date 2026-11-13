# Fair Value

Portfolio-oriented currency valuation monitors. The backend acquires data and
calculates each snapshot; the frontend is a separate static website.

- [USDCAD dashboard](USDCAD/README.md): selected two-layer model from
  `case-study/USDCAD`, allocation revision 18.
- [Repository agent instructions](AGENT.MD)

The backend is a once-daily agent batch job. It pushes calculated data to this
repository; GitHub Pages publishes the static frontend and JSON together.
Read [the daily agent procedure](USDCAD/DAILY_UPDATE.md). The same static output
is compatible with Cloudflare Pages if the hosting destination changes later.
