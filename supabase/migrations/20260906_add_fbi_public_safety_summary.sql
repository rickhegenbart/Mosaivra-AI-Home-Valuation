create table if not exists public.fbi_public_safety_summary (
    scope_key text not null,
    report_year integer not null,

    geography_name text not null,
    geography_level text not null,
    state_abbreviation text not null default 'MT',
    county_fips text not null default '30111',
    ori_codes text[] not null,

    covered_population integer not null,

    violent_crime_count integer not null,
    violent_crime_rate_per_1000 numeric not null,

    property_crime_count integer not null,
    property_crime_rate_per_1000 numeric not null,

    homicide_count integer not null,
    rape_count integer not null,
    robbery_count integer not null,
    aggravated_assault_count integer not null,

    burglary_count integer not null,
    larceny_count integer not null,
    motor_vehicle_theft_count integer not null,
    arson_count integer not null,

    source_name text not null
        default 'FBI Crime Data Explorer',
    source_url text not null,
    source_period text not null,
    source_date date,

    confidence_level text not null
        default 'context_only',
    notes text,
    updated_at timestamp with time zone not null
        default now(),

    constraint fbi_public_safety_summary_pkey
        primary key (scope_key, report_year),

    constraint fbi_public_safety_summary_scope_check
        check (
            scope_key in (
                'billings_police',
                'yellowstone_participating_agencies'
            )
        ),

    constraint fbi_public_safety_summary_year_check
        check (
            report_year between 1991 and 2100
        ),

    constraint fbi_public_safety_summary_population_check
        check (covered_population > 0),

    constraint fbi_public_safety_summary_counts_check
        check (
            violent_crime_count >= 0
            and property_crime_count >= 0
            and homicide_count >= 0
            and rape_count >= 0
            and robbery_count >= 0
            and aggravated_assault_count >= 0
            and burglary_count >= 0
            and larceny_count >= 0
            and motor_vehicle_theft_count >= 0
            and arson_count >= 0
        )
);

create index if not exists
    idx_fbi_public_safety_summary_year
on public.fbi_public_safety_summary (
    report_year desc
);

create index if not exists
    idx_fbi_public_safety_summary_county
on public.fbi_public_safety_summary (
    county_fips
);

comment on table public.fbi_public_safety_summary is
    'Annual FBI Crime Data Explorer summarized reported-offense context for Billings Police and participating Yellowstone County agencies.';

comment on column
    public.fbi_public_safety_summary.violent_crime_rate_per_1000
is
    'Reported violent-crime offenses divided by covered population and multiplied by 1,000.';

comment on column
    public.fbi_public_safety_summary.property_crime_rate_per_1000
is
    'Reported property-crime offenses divided by covered population and multiplied by 1,000.';