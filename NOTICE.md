# Third-party data included in this repository

This project ships two sentiment lexicons. Both allow redistribution; the
attribution below is what their licences require.

## SentiLex-PT02 — `lexicon/SentiLex-flex-PT02.txt`, `lexicon/SentiLex-lem-PT02.txt`

Paula Carvalho and Mário J. Silva.
**Licence: Creative Commons Attribution 4.0 (CC BY 4.0).**
https://doi.org/10.23728/b2share.93ab120efdaa4662baec6adee8e7585f

> Carvalho, P., Silva, M. J. (2015). SentiLex-PT02.

## VADER lexicon — `lexicon/vader_lexicon.txt`

C.J. Hutto. **Licence: MIT.** https://github.com/cjhutto/vaderSentiment

> Hutto, C.J. & Gilbert, E.E. (2014). VADER: A Parsimonious Rule-based Model for
> Sentiment Analysis of Social Media Text. Eighth International Conference on
> Weblogs and Social Media (ICWSM-14).

## News content

Article headlines, summaries and links belong to the outlets that published
them. The database (`data/news.db`, not tracked here) holds only those three
fields, fetched from each outlet's public RSS feed, and drops articles after
14 days (120 for the independent outlets).
