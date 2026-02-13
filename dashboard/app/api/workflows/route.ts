
import { NextResponse } from "next/server";
import { Octokit } from "octokit";

export async function GET() {
    const token = process.env.GITHUB_TOKEN;
    const owner = "bigodinhc";
    const repo = "workflows_minerals";

    if (!token) {
        return NextResponse.json({ error: "Missing GITHUB_TOKEN" }, { status: 500 });
    }

    const octokit = new Octokit({ auth: token });

    try {
        // 1. List workflow runs
        const { data: runs } = await octokit.request("GET /repos/{owner}/{repo}/actions/runs", {
            owner,
            repo,
            per_page: 100, // Increased to show more history
        });

        // 2. Map to format
        const simplifiedRuns = runs.workflow_runs.map((run) => ({
            id: run.id,
            status: run.status,
            conclusion: run.conclusion,
            name: run.name,
            event: run.event,
            created_at: run.created_at,
            updated_at: run.updated_at,
            html_url: run.html_url,
            run_number: run.run_number,
            duration: run.updated_at && run.created_at
                ? (new Date(run.updated_at).getTime() - new Date(run.created_at).getTime()) / 1000
                : null,
            commit: {
                message: run.head_commit?.message,
                author: run.head_commit?.author?.name,
                sha: run.head_sha
            }
        }));

        return NextResponse.json(simplifiedRuns);
    } catch (error) {
        console.error("GitHub API Error:", error);
        return NextResponse.json({ error: "Failed to fetch workflow runs" }, { status: 500 });
    }
}

export async function POST(req: Request) {
    // Trigger workflow dispatch
    const token = process.env.GITHUB_TOKEN;
    const owner = "bigodinhc";
    const repo = "workflows_minerals";

    if (!token) return NextResponse.json({ error: "Missing Token" }, { status: 500 });

    // Read body
    let workflow_id = "daily_report.yml";
    let inputs = {};

    try {
        const body = await req.json();
        if (body.workflow_id) workflow_id = body.workflow_id;
        if (body.inputs) inputs = body.inputs;
    } catch (e) {
        // ignore if empty body
    }

    const octokit = new Octokit({ auth: token });

    try {
        await octokit.request('POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches', {
            owner,
            repo,
            workflow_id,
            ref: 'main',
            inputs: {
                dry_run: 'false',
                ...inputs
            }
        });
        return NextResponse.json({ success: true, message: `Workflow ${workflow_id} triggered` });
    } catch (error) {
        console.error("Trigger Error:", error);
        return NextResponse.json({ error: "Failed to trigger workflow" }, { status: 500 });
    }
}
