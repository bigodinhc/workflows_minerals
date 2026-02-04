
import { NextResponse } from "next/server";
import { google } from "googleapis";

export async function GET() {
    const credsJson = process.env.GOOGLE_CREDENTIALS_JSON;
    const sheetId = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0";

    if (!credsJson) {
        console.error("Missing GOOGLE_CREDENTIALS_JSON");
        return NextResponse.json({ error: "Missing Google Credentials" }, { status: 500 });
    }

    try {
        let credentials;
        try {
            // Try parsing JSON directly
            credentials = JSON.parse(credsJson);
        } catch (e) {
            // Try decoding base64 if it fails (common in env vars)
            const buff = Buffer.from(credsJson, 'base64');
            credentials = JSON.parse(buff.toString('utf-8'));
        }

        const auth = new google.auth.GoogleAuth({
            credentials,
            scopes: ["https://www.googleapis.com/auth/spreadsheets.readonly"],
        });

        const sheets = google.sheets({ version: "v4", auth });

        const response = await sheets.spreadsheets.values.get({
            spreadsheetId: sheetId,
            range: "A:Z", // Get all data
        });

        const rows = response.data.values;
        if (!rows || rows.length === 0) {
            return NextResponse.json([]);
        }

        // Assume headers are in the first row
        const headers = rows[0];
        const data = rows.slice(1).map((row, index) => {
            const obj: any = { id: index + 2 }; // Excel Row Number
            headers.forEach((header, i) => {
                obj[header] = row[i];
            });
            return obj;
        });

        return NextResponse.json(data);

    } catch (error) {
        console.error("Google Sheets API Error:", error);
        return NextResponse.json({ error: "Failed to fetch spreadsheet" }, { status: 500 });
    }
}
