
import { NextResponse } from "next/server";
import { Octokit } from "octokit";

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
        // 1. Get Jobs for the run
        const { data: jobs } = await octokit.request('GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs', {
            owner,
            repo,
            run_id: Number(run_id)
        });

        // Use the first job (usually 'check-and-send' or 'build')
        const job = jobs.jobs[0];
        if (!job) {
            return NextResponse.json({ error: "No jobs found for this run" }, { status: 404 });
        }

        // 2. Fetch Logs for the Job
        // GitHub API redirects to the raw log file. Octokit follows redirects by default?
        // Actually, for LOGS, typically we try to download.
        // let's try reading as text.

        const response = await octokit.request('GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs', {
            owner,
            repo,
            job_id: job.id,
            mediaType: {
                format: "raw"
            }
        });

        // The response.data should be the log text because we requested raw
        return new NextResponse(String(response.data), {
            headers: { "Content-Type": "text/plain" }
        });

    } catch (error) {
        console.error("Logs Fetch Error:", error);
        return NextResponse.json({ error: "Failed to fetch logs" }, { status: 500 });
    }
}
