import { NextResponse } from "next/server";
import { Octokit } from "octokit";

type DeliveryResult = {
  name: string;
  phone: string;
  success: boolean;
  error: string | null;
  duration_ms: number;
};

type DeliveryReportPayload = {
  workflow: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  summary: { total: number; success: number; failure: number };
  results: DeliveryResult[];
};

const MARKER_START = "<<<DELIVERY_REPORT_START>>>";
const MARKER_END = "<<<DELIVERY_REPORT_END>>>";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const run_id = searchParams.get("run_id");

  const token = process.env.GITHUB_TOKEN;
  const owner = "bigodinhc";
  const repo = "workflows_minerals";

  if (!token) return NextResponse.json({ error: "Missing Token" }, { status: 500 });
  if (!run_id) return NextResponse.json({ error: "Missing run_id" }, { status: 400 });

  const octokit = new Octokit({ auth: token });

  try {
    const { data: jobs } = await octokit.request(
      "GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
      { owner, repo, run_id: Number(run_id) }
    );
    const job = jobs.jobs[0];
    if (!job) {
      return NextResponse.json({ found: false, report: null });
    }
    const response = await octokit.request(
      "GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
      { owner, repo, job_id: job.id, mediaType: { format: "raw" } }
    );
    const logText = String(response.data);

    const startIdx = logText.indexOf(MARKER_START);
    const endIdx = logText.indexOf(MARKER_END);
    if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
      return NextResponse.json({ found: false, report: null });
    }

    const jsonBlock = logText
      .slice(startIdx + MARKER_START.length, endIdx)
      .trim();
    // GH Actions prefixes each line with timestamps. Strip them.
    const cleanedJson = jsonBlock
      .split("\n")
      .map((line) => line.replace(/^\S+\s+/, "")) // drop leading "2026-04-13T14:31:22.1234567Z "
      .join("\n");

    let parsed: DeliveryReportPayload;
    try {
      parsed = JSON.parse(cleanedJson);
    } catch (e) {
      console.error("Failed to parse delivery report JSON:", e);
      return NextResponse.json({ found: false, report: null });
    }

    return NextResponse.json({ found: true, report: parsed });
  } catch (error) {
    console.error("Delivery report fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch report" }, { status: 500 });
  }
}
