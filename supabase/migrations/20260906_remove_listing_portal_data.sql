begin;

drop view if exists public.proxy_training_data;
drop view if exists public.latest_market_snapshot;

drop table if exists public.realtor_county_market_indicators;

create view public.latest_market_snapshot as
with preferred_hpi as (
    select
        hpi.period_start_date,
        hpi.geo_scope,
        hpi.geography_name,
        hpi.hpi_index,
        hpi.hpi_yoy_change_pct,
        hpi.hpi_period_change_pct
    from public.hpi_market_indicators as hpi
    where hpi.geo_scope in (
        'billings_msa',
        'montana_state'
    )
      and hpi.hpi_type = 'traditional'
      and hpi.hpi_flavor = 'all-transactions'
      and hpi.frequency = 'quarterly'
    order by
        hpi.period_start_date desc,
        case
            when hpi.geo_scope = 'billings_msa' then 1
            when hpi.geo_scope = 'montana_state' then 2
            else 3
        end
    limit 1
),
latest_mortgage as (
    select
        mortgage.indicator_date,
        mortgage.mortgage_rate,
        mortgage.mortgage_rate_4week_avg,
        mortgage.mortgage_rate_13week_avg,
        mortgage.mortgage_rate_change_52week
    from public.mortgage_rate_indicators as mortgage
    order by mortgage.indicator_date desc
    limit 1
),
latest_unemployment as (
    select
        unemployment.indicator_date,
        unemployment.unemployment_rate,
        unemployment.unemployment_rate_3month_avg,
        unemployment.unemployment_rate_12month_avg,
        unemployment.unemployment_pressure_score
    from public.unemployment_rate_indicators as unemployment
    order by unemployment.indicator_date desc
    limit 1
)
select
    hpi.period_start_date as hpi_date,
    hpi.geo_scope,
    hpi.geography_name as hpi_geography_name,
    hpi.hpi_index,
    hpi.hpi_yoy_change_pct,
    hpi.hpi_period_change_pct,
    mortgage.indicator_date as mortgage_date,
    mortgage.mortgage_rate,
    mortgage.mortgage_rate_4week_avg,
    mortgage.mortgage_rate_13week_avg,
    mortgage.mortgage_rate_change_52week,
    unemployment.indicator_date as unemployment_date,
    unemployment.unemployment_rate,
    unemployment.unemployment_rate_3month_avg,
    unemployment.unemployment_rate_12month_avg,
    unemployment.unemployment_pressure_score
from preferred_hpi as hpi
cross join latest_mortgage as mortgage
cross join latest_unemployment as unemployment;

create view public.proxy_training_data as
select
    parcel.parcel_id,
    parcel.property_id,
    parcel.address_line_1,
    parcel.site_city,
    parcel.site_state,
    parcel.site_zip_code,
    parcel.county_name,
    parcel.property_type,
    parcel.property_type_group,
    parcel.model_segment,
    parcel.is_residential,
    parcel.gis_acres,
    parcel.total_acres,
    parcel.lot_size_sqft,
    parcel.total_land_value,
    parcel.total_building_value,
    parcel.total_value,
    parcel.latitude,
    parcel.longitude,
    parcel.tax_year,
    parcel.source_date,
    market.hpi_index,
    market.hpi_yoy_change_pct,
    market.hpi_period_change_pct,
    market.mortgage_rate,
    market.mortgage_rate_4week_avg,
    market.mortgage_rate_13week_avg,
    market.mortgage_rate_change_52week,
    market.unemployment_rate,
    market.unemployment_rate_3month_avg,
    market.unemployment_rate_12month_avg,
    market.unemployment_pressure_score,
    parcel.total_value as target_proxy_value
from public.parcel_model_segments as parcel
cross join public.latest_market_snapshot as market
where parcel.total_value is not null
  and parcel.total_value > 0
  and parcel.lot_size_sqft is not null
  and parcel.lot_size_sqft > 0
  and parcel.model_segment in (
      'land',
      'residential',
      'improved_unknown',
      'commercial_or_income',
      'industrial',
      'agricultural'
  );

grant select
on public.latest_market_snapshot
to anon, authenticated, service_role;

grant select
on public.proxy_training_data
to anon, authenticated, service_role;

delete from public.data_pipeline_runs
where job_name = 'update_realtor_inventory'
   or source_name ilike '%realtor%'
   or source_name ilike '%zillow%';

commit;
