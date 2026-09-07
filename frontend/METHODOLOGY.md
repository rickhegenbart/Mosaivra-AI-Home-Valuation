Mosaivra AI Methodology

Last updated: September 6, 2026

1. What Mosaivra AI is

Mosaivra AI is a public-data real estate parcel analysis tool for Yellowstone County, Montana. It produces an Estimated Public Parcel Value using public parcel characteristics, location fields, and government economic indicators.

Mosaivra AI is designed as a decision-support and screening tool. It is not an appraisal, comparative market analysis (CMA), inspection, insurance assessment, or guarantee of market or sale value.

2. What the model predicts

The model predicts a public-record-based target derived from the parcel dataset:

target_proxy_value = total_value

In plain language, the model estimates the parcel's public total value as represented in the source parcel records. The output is therefore labeled:

Estimated Public Parcel Value

It should not be described as:

Appraised value
Comparative market analysis
Verified market value
Sale-price prediction
Guaranteed sale price

3. Prediction flow

The user searches for an address, city, or parcel ID.

The React frontend sends the request to the FastAPI backend.

The backend retrieves the matching public parcel record from Supabase.

The backend prepares the same 25 features used during training.

The compressed scikit-learn model predicts an estimated public parcel value.

The backend applies a segment-based range and confidence label.

The frontend displays the estimate, range, parcel details, model notes, and disclaimers.

Separate public-data context layers are displayed for additional location information but do not alter the prediction.

4. Current model inputs

The deployed model uses 25 input features. It does not use third-party listing-portal datasets, private listing-service data, or closed-sale feeds.

4.1 Parcel size and location

gis_acres
total_acres
lot_size_sqft
latitude
longitude
tax_year

These fields help the model learn how parcel size, location, and tax-year context relate to public parcel value.

4.2 FHFA Housing Price Index indicators

hpi_index
hpi_yoy_change_pct
hpi_period_change_pct

These indicators come from the Federal Housing Finance Agency and describe broader housing-price movement.

4.3 FRED mortgage-rate indicators

mortgage_rate
mortgage_rate_4week_avg
mortgage_rate_13week_avg
mortgage_rate_change_52week

These indicators describe the national residential-financing environment.

4.4 FRED unemployment indicators

unemployment_rate
unemployment_rate_3month_avg
unemployment_rate_12month_avg
unemployment_pressure_score

These indicators describe broader labor-market and economic conditions.

4.5 Categorical location and property classification

site_city
site_state
site_zip_code
county_name
property_type
property_type_group
model_segment
is_residential

These features distinguish different locations, public property classifications, and modeling segments.

4.6 Important limitation of current economic features

The current training view joins the latest economic snapshot to the parcel records. Indicators that are identical across all training rows provide model-version and economic context but cannot explain differences between parcels within that snapshot. A future time-aware training dataset with historical parcel observations would be needed to estimate how changing economic conditions affect values over time.

5. Inputs the model does not use

The model does not currently use:

Bedrooms
Bathrooms
Finished building square footage
Garage size
Year built
Interior condition
Renovation status
Property photographs
Private comparable sales
Closed-sale history
Current asking price
Current listing status
Seller motivation
Inspection findings
Private consumer information

The following target-related public valuation fields are also excluded as model inputs:

total_value
total_land_value
total_building_value

This prevents the model from simply copying the public target or its direct components back into the prediction.

6. Model segments

Current modeling segments include:

residential
land
improved_unknown
commercial_or_income
industrial
agricultural

Segment

Meaning

residential

Clearly residential records, including supported condominium and townhouse classifications.

land

Vacant land parcels.

improved_unknown

Parcels with buildings or improvements where the public source does not clearly distinguish residential from commercial use.

commercial_or_income

Commercial, income, multifamily, mixed-use, or similar parcels.

industrial

Industrial property records.

agricultural

Agricultural or farmstead-related records.

The model segment also informs the range and confidence language returned by the backend.

7. Model training

The deployed model is a TransformedTargetRegressor containing a scikit-learn preprocessing and random-forest pipeline.

Training configuration:

Random forest trees: 250
Maximum tree depth: 22
Minimum samples per leaf: 3
Maximum features per split: square root
Training/test split: 80% / 20%
Random state: 42
Maximum training target: $5,000,000
Target transformation: log1p / expm1

Numeric missing values are imputed with the median. Categorical missing values are imputed with the most frequent value and categories are one-hot encoded. Unknown categories encountered during inference are ignored safely.

8. Current model evaluation

The no-listing-data model was trained on 78,302 cleaned parcel rows:

Training rows: 62,641
Testing rows: 15,661
Mean absolute error: $91,225
Median absolute error: $36,724
Root mean squared error: $234,107
R²: 0.477
Predictions within 10%: 33.29%
Predictions within 15%: 45.40%
Predictions within 20%: 54.75%

The model passed the project’s replacement guardrails:

Mean absolute error remained within 10% of the prior model.

R² remained within 0.05 of the prior model.

The percentage of predictions within 20% remained within five percentage points of the prior model.

These results support an MVP screening tool, not appraisal-style claims. Performance varies substantially by property segment and individual parcel.

9. Prediction range and confidence

The backend returns a central estimate and a low-to-high decision-support range. The range is not a statistical appraisal confidence interval.

Typical interpretation:

Segment

Range interpretation

residential

Generally narrower because the public classification is clearer.

improved_unknown

Wider because the building use is unclear.

land

Wider because limited public features may not describe development potential or site constraints.

commercial_or_income

Wider because income, tenancy, operating expenses, and capitalization rates are unavailable.

industrial

Wider because parcels may be specialized and the sample is smaller.

agricultural

Wider because productivity, water rights, improvements, and special uses may be unavailable.

10. Why the estimate may differ from market or sale value

A real transaction can be affected by information unavailable to the model, including:

Interior condition
Renovations
Deferred maintenance
Functional layout
Building size and quality
Curb appeal
Buyer and seller motivation
Financing terms
Inspection outcomes
Appraisal conditions
Marketing exposure
Competing properties
Off-market terms

The output is therefore a public-data proxy, not a market-value or sale-price forecast.

11. Public-data context layers

The parcel result also displays automated context layers. These are not model features and do not change the estimated public parcel value.

Context layer

Geography

Primary source

Demographic and housing context

Census tract

U.S. Census Bureau ACS 5-Year Estimates

Environmental hazard context

Census tract, with supported fallback

FEMA National Risk Index

Historical storm events

County

NOAA Storm Events Database

Federal disaster declaration history

County

OpenFEMA Disaster Declarations Summaries

School enrollment and staffing

Coordinate-matched school district

Census TIGER/Line and NCES Common Core of Data

Public-safety statistics

Reporting agency or participating county agency group

FBI Crime Data Explorer

Rental benchmark context

HUD Fair Market Rent area

HUD Fair Market Rents

Construction-cost context

National

BLS Producer Price Index data distributed through FRED

These layers are descriptive public context. They are not parcel inspections, neighborhood rankings, safety scores, school-quality scores, political-opinion scores, insurance determinations, or valuation adjustments.

12. Data refresh and reproducibility

Automated pipelines update the government and public-source datasets on source-appropriate schedules using GitHub Actions. Pipeline runs are recorded in Supabase with status, source period, row counts, validation summaries, and error messages.

The model is not automatically retrained whenever an upstream indicator changes. Retraining is a controlled process that produces candidate artifacts, evaluates them against guardrails, and requires validation before production promotion.

The production model package includes:

price_model_compressed.joblib
feature_columns.json
model_metadata.json
model_metrics.json
training_feature_importance.csv
segment_model_metrics.csv
model_training_sample_predictions.csv
validation_report.json

13. Recommended product language

Use:

Estimated Public Parcel Value
Public Parcel Value Proxy
Decision-support estimate
Public-data-based estimate range

Avoid:

Appraisal
Comparative market analysis
Guaranteed value
True market value
Sale-price prediction

14. Required disclaimer

This estimate is a public-data-based parcel value proxy. It is based on public parcel records, property classification, lot size, location, and government economic context. It is not an appraisal, comparative market analysis, inspection, insurance assessment, or guaranteed sale-price estimate and should be used for decision support only.

15. Recommended improvements

High-value future improvements include:

Add building square footage, bedrooms, bathrooms, age, and other property characteristics when an authorized public source is available.

Train and tune separate models by property segment.

Improve the vacant-land model with zoning, access, utilities, topography, and service-distance features.

Add permitted floodplain, zoning, and land-use features to model development constraints.

Build historical training observations so economic changes can be modeled across time.

Calibrate prediction ranges using empirical error by segment and value band.

Add automated drift monitoring, version comparison, and retraining logs.

Evaluate results against an authorized, representative validation dataset that is independent of the public assessment target.

16. Summary

Mosaivra AI estimates a public parcel value using 25 features derived from public parcel records, public property classifications, FHFA housing indicators, and FRED economic indicators. It supplements the estimate with automated public-data context layers that remain separate from the trained model.

The system is most useful for early-stage screening, comparison, and public-data exploration. It should not replace an appraisal, professional comparative analysis, inspection, insurance review, or other qualified real-estate judgment.