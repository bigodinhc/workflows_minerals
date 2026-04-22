import { NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";

export async function GET() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_KEY;

  if (!url || !key) {
    console.error("Missing SUPABASE_URL or SUPABASE_KEY");
    return NextResponse.json(
      { error: "Supabase not configured" },
      { status: 500 },
    );
  }

  try {
    const supabase = createClient(url, key);
    const { data, error } = await supabase
      .from("contacts")
      .select("id, name, phone_raw, phone_uazapi, status, created_at, updated_at")
      .order("created_at", { ascending: false });

    if (error) {
      console.error("Supabase error:", error);
      return NextResponse.json(
        { error: "Failed to fetch contacts" },
        { status: 500 },
      );
    }

    return NextResponse.json(data || []);
  } catch (error) {
    console.error("Dashboard contacts route error:", error);
    return NextResponse.json(
      { error: "Unexpected error" },
      { status: 500 },
    );
  }
}
