create table if not exists
public.fema_disaster_declaration_summary (
    county_fips text primary key,
    state_abbreviation text not null,
    geography_name text not null,
    geography_level text not null
        default 'county',

    total_declaration_count integer not null,
    biological_declaration_count integer not null
        default 0,
    fire_declaration_count integer not null
        default 0,
    flood_declaration_count integer not null
        default 0,
    hurricane_declaration_count integer not null
        default 0,
    severe_storm_declaration_count integer not null
        default 0,
    other_declaration_count integer not null
        default 0,

    earliest_declaration_date timestamptz,
    latest_declaration_date timestamptz,
    latest_disaster_number integer,
    latest_declaration_type text,
    latest_incident_type text,
    latest_declaration_title text,
    latest_designated_area text,
    latest_source_refresh timestamptz,
    latest_declaration_year integer,

    source_name text not null,
    source_url text not null,
    source_period text not null,
    source_date timestamptz,
    confidence_level text not null
        default 'context_only',
    notes text,
    updated_at timestamptz not null
        default now(),

    constraint
        fema_disaster_summary_county_fips_check
        check (
            county_fips ~ '^[0-9]{5}$'
        ),

    constraint
        fema_disaster_summary_counts_check
        check (
            total_declaration_count >= 0
            and biological_declaration_count >= 0
            and fire_declaration_count >= 0
            and flood_declaration_count >= 0
            and hurricane_declaration_count >= 0
            and severe_storm_declaration_count >= 0
            and other_declaration_count >= 0
        ),

    constraint
        fema_disaster_summary_total_check
        check (
            total_declaration_count =
                biological_declaration_count
                + fire_declaration_count
                + flood_declaration_count
                + hurricane_declaration_count
                + severe_storm_declaration_count
                + other_declaration_count
        )
);

create index if not exists
idx_fema_disaster_declaration_summary_year
on public.fema_disaster_declaration_summary (
    latest_declaration_year
);

comment on table
public.fema_disaster_declaration_summary
is
'Automated county-level summary of federal disaster declarations from OpenFEMA. Context only; not a parcel-specific risk or valuation measure.';

alter table
public.fema_disaster_declaration_summary
enable row level security;

drop policy if exists
"Allow public read access to FEMA disaster summaries"
on public.fema_disaster_declaration_summary;

create policy
"Allow public read access to FEMA disaster summaries"
on public.fema_disaster_declaration_summary
for select
to anon, authenticated
using (true);