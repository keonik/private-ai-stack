// Ground truth from an existing rule-based parser kept in a separate repository: runs its OH-1 parser on each
// PDF and prints one JSON line per file. Usage:
//   RULE_PARSER_REPO=/path/to/parser bun run scripts/rule_parser_dump.ts FILES... > parser.jsonl
// Output per file: {file, reportNumber, date (UTC ISO), county, countyCode, officerName, unitInErrorNumber,
//   units:[{number, type, injurySeverity (1 fatal .. 5 none), inError}], narrativeLen, error}
const GP = process.env.RULE_PARSER_REPO ?? "";
if (!GP) throw new Error("set RULE_PARSER_REPO to the checkout holding the rule-based parser");
const { parseDocs, projectReport } = await import(`${GP}/shared/src/crash/fixture-shape`);
for (const f of process.argv.slice(2)) {
  const proc = Bun.spawn(["bun", "run", `${GP}/server/src/workers/pdf-parse.ts`], { cwd: `${GP}/server`, stdin: "pipe", stdout: "pipe", stderr: "ignore" });
  proc.stdin.write(JSON.stringify({ filePath: f })); proc.stdin.end();
  const out = await new Response(proc.stdout).text(); await proc.exited;
  let rec: any = { file: f.split("/").pop(), error: null };
  try {
    const r = parseDocs(JSON.parse(out), rec.file);
    rec = { ...rec, ...projectReport(r), units: (r?.units ?? []).map((u: any) => ({ number: u.number, type: u.type, injurySeverity: u.injurySeverity, inError: u.inError })) };
    rec.narrativeLen = r?.narrative?.length ?? 0;
  } catch (e) { rec.error = String(e).slice(0, 200); }
  console.log(JSON.stringify(rec));
}
