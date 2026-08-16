// The site renders and nothing more. It polls the results file and the event
// tape and computes no metric of its own -- if a sheet needs a number, Python
// put it in results.json.
import { defineConfig } from "astro/config";

export default defineConfig({
  server: { port: 4321, host: true },
  output: "static",
});
