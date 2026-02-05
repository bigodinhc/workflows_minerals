
import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import { exec } from 'child_process';
import util from 'util';

const execPromise = util.promisify(exec);
const DRAFTS_FILE = path.join(process.cwd(), '../data/news_drafts.json');

// Helper to read drafts
function getDrafts() {
    if (!fs.existsSync(DRAFTS_FILE)) return [];
    try {
        const data = fs.readFileSync(DRAFTS_FILE, 'utf-8');
        return JSON.parse(data);
    } catch (e) {
        return [];
    }
}

// Helper to save drafts
function saveDrafts(drafts: any[]) {
    // Ensure directory exists
    const dir = path.dirname(DRAFTS_FILE);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

    fs.writeFileSync(DRAFTS_FILE, JSON.stringify(drafts, null, 2));
}

export async function GET() {
    const drafts = getDrafts();
    // Filter for pending drafts, sort by newest
    const pending = drafts
        .filter((d: any) => d.status === 'pending')
        .sort((a: any, b: any) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

    return NextResponse.json({ drafts: pending });
}

export async function POST(req: Request) {
    try {
        const body = await req.json();
        const { action, draftId, text } = body;

        let drafts = getDrafts();
        const index = drafts.findIndex((d: any) => d.id === draftId);

        if (index === -1) {
            return NextResponse.json({ error: 'Draft not found' }, { status: 404 });
        }

        if (action === 'approve') {
            // 1. Update status
            drafts[index].status = 'approved';
            drafts[index].ai_text = text;
            drafts[index].approved_at = new Date().toISOString();
            saveDrafts(drafts);

            // 2. Trigger Sending Script
            // Write message to a temp file to avoid CLI argument length limits/shell injection
            const tempMsgFile = path.join(process.cwd(), '../data', `msg_${draftId}.txt`);
            fs.writeFileSync(tempMsgFile, text);

            // We pass the content as argument for now as our script takes --message
            // BUT for safety with special chars/newlines, input file is better.
            // Let's modify the script OR just use subprocess properly.
            // Since send_news.py takes --message arg, we must be careful with quotes.
            // Better approach: Update send_news.py to accept --file argument? 
            // Or just assume local env and simple content. 
            // Let's use the --message for now but wrap it carefully.

            // Actually, exec is dangerous with big texts. 
            // Let's update send_news.py to support reading from file?
            // No, let's keep it simple: we can call it passing the text.
            // Wait, complex text with newlines in shell args is pain.

            // Update: I will use the `python3 execution/scripts/send_news.py --message "TEXT"` approach
            // but simplistic exec might fail on quotes.

            // Let's use a simpler approach: 
            // Create a python helper here in-line? No.
            // Write to file `msg.txt` and have python read it? Yes.

            // Let's assume send_news.py can read from file if I add --file arg.
            // I will update send_news.py in next step. For now let's write the execution call assuming --file

            const scriptPath = path.join(process.cwd(), '../execution/scripts/send_news.py');
            const cmd = `python3 "${scriptPath}" --file "${tempMsgFile}"`;

            try {
                await execPromise(cmd);
                // Cleanup
                if (fs.existsSync(tempMsgFile)) fs.unlinkSync(tempMsgFile);
            } catch (err: any) {
                console.error("Failed to execute sending script:", err);
                return NextResponse.json({ error: 'Failed to send: ' + err.message }, { status: 500 });
            }

            return NextResponse.json({ success: true, message: 'Draft approved and SENT successfully.' });

        } else if (action === 'reject') {
            drafts[index].status = 'rejected';
            saveDrafts(drafts);
            return NextResponse.json({ success: true });
        }

        return NextResponse.json({ error: 'Invalid action' }, { status: 400 });

    } catch (e) {
        return NextResponse.json({ error: String(e) }, { status: 500 });
    }
}
