# Race list review (before pre-registration, Tue 3 Nov 5:00pm ET)

`races.json` was drafted on 31 Oct from Ballotpedia, state and county election offices, and news, then merged with `scripts/build_races.py`. Every entry has `review.status: "draft"`. A person checks each race against the state election office's certified candidate list, then sets `status: "checked"` and adds their name to `checked_by`. One name per race (decided 3 Nov; Senate and Governor needed two before).

## Check these first (sources disagree or are incomplete)

| Race | Question |
|---|---|
| senate-ak | Daniel J. Sullivan Jr.: party unknown (Ballotpedia/NBC say R, CBS says none on the ballot). DOJ asked Alaska to segregate ballots ranking him; is he still on the ballot? |
| senate-il, senate-mi, senate-nc | Candidate lists came from summaries and news, because the candidate tables were cut off. Check against the SOS lists. |
| senate-ar, senate-co, senate-de | Ballotpedia says its list "may not be complete" |
| governor-ct | Adgers and Noe (I): left out as write-ins, but Ballotpedia's summary lists them |
| governor-wi | Grimek (Constitution): left out as a write-in; one Ballotpedia sentence lists him |
| governor-az, -co, -fl, -id, -il, -tx | Ballotpedia says its list "may not be complete" |
| house-az-06 | Dorland (I): left out as a write-in, but the Cochise County results page lists him |
| house-ky-06 | Quigley (I): left out as a write-in, but the Kentucky Lantern guide lists him |
| house-wi-03 | Kent and Provance (I): included; one paper calls them write-ins |
| house-va-02 | Luria's lawsuit to remove Staten (I) from the ballot: outcome unknown |
| house-mn-01 | Ballotpedia shows 2 candidates, one aggregator says 3 |
| house-oh-07, house-tx-09 | Ballotpedia list "may not be complete" |
| house-nj-07 | O'Toole: "Stop Israel's Genocide" is a petition slogan, coded I |
| house-sc-01 | Ballot names: "Jenny Costa Honeycutt"? "LaCore" spelling? |
| house-ne-01 | "Dan" or "Daniel" Hartong |

## Ballotpedia plus news only (no official list checked)

- House: AK-AL, AZ-01, CA-13, CA-48, CO-05, FL-27, IA-03, MI-10, MO-02, NE-02, NJ-07, NV-04, OH-01, OH-10, PA-01, PA-10, WA-03, AL-02, CA-22, IN-01, KY-06, MN-01, MT-01, NJ-09, NV-02, NY-03, NY-19, OH-07, PA-07, SC-01, WA-05.
- Batch 3's House races (AR-02 through WI-01) are from Ballotpedia's general tables, with official cross-checks for NY-04, NY-21, VA-07, AZ-06 and MI-08 only.

## Confirmed but surprising (no action, but don't "fix" them)

- **senate-sc:** Lindsey Graham died in Jul 2026. His sister Darline Graham (also "Darline Graham Nordone") was appointed and is the R nominee and incumbent.
- **senate-ok:** Mullin became DHS Secretary. Hern (R) is the nominee; no incumbent is on the ballot.
- **No incumbent on the ballot:** senate-tx (Paxton beat Cornyn), senate-la (Letlow beat Cassidy), house-fl-07 (Mills lost the primary), house-nc-11 (Edwards withdrew; Balkcom replaced him), house-tx-09 (Green isn't running).
- **senate-me:** Troy Jackson replaced Platner as the D nominee.
- **governor-ak:** Tom Begich withdrew and is off the ballot.
- **house-tx-23:** vacant since Gonzales resigned on 14 Apr. No special election on 3 Nov.
- **New maps in 2026:** Florida, Texas, North Carolina, Ohio and Alabama drew new maps, and CA-45's boundaries changed. District numbers follow the new maps, as Cook's do (e.g. Moskowitz is incumbent in the new FL-25, and Frankel runs in FL-23, so FL-22 is open).

## Races where Tuesday night may produce no winner

- **Runoff:** senate-ga and governor-ga go to a runoff on 1 Dec if no one passes 50%. In governor-vt, the legislature picks the winner if no one gets a majority.
- **Ranked choice:** senate-ak, governor-ak, house-ak-al, senate-me and house-me-02.
- **Top-two, slow counts:** house-ca-13, house-ca-22, house-ca-45, house-ca-48, house-wa-03 and house-wa-05.

## For the winner detector (not a list error)

- **Two candidates share a surname in the same race:** senate-ak (Sullivan) and governor-nv (Ford). Match full names.
- **Common surnames:** Smith, Lee, Hill, Green, Flood, Best, Crank, Johnson, Miller, Brown, Garcia and others.
  - The cheap filter can be loose.
  - The model check must name the race and the year.
- **Surnames shared across races:** Collins (GA/ME), Booker (KY/NJ), Brown (OH/TX), Graham (SC/KS) and Shaheen (NH-01 vs Sen. Jeanne Shaheen).
- **Variants to accept:** Luján/Lujan, Gluesenkamp Perez/Perez, Padilla Stout/Stout, Trone Garriott and von Wilpert.

## Poll close rule

The latest scheduled close in the state, or in the district for House races. AP "will not declare winners in statewide races before the last polls have closed." Sources are in `scripts/build_races.py`.
