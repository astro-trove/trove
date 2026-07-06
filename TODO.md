We have a lot of these different distance metrics but are currently just testing them against "simulated" data. I think a better way to compare them all is with the real data that we have. That being said, maybe you can implement each of the following distance scores in TROVE individually (maybe just on different branches?) and run the host association + distance scoring (that's all, you wouldn't need to run the full scoring/vetting procedure) them on some real candidates from GW events. I think a good start would be with S251112cm, since @Nick Vieira wrote a nice paper on it and it include Rubin discovered transients! But, another good GW event to test with would be GW170817, since we know the answer (i.e., AT2017gfo should get a good distance score!).

Here are the scores I would implement:

The Bhattacharyya coefficient
The normalized Bhattacharyya coefficient
The standard "z-score" (the probability that the median of the galaxy distribution is in the gaussian GW PDF)
The "resampled z-score" (see the code I sent)
Your "discretized jsd_uniform"

If you want you can also try the downweighted JSD using a uniform distribution, although I'm not sure that is producing the results we want. Once you have the distnace scores from each of these then make a bunch of plots of them. I think the key plot that I am envisioning is for each method a histogram of the distance scores with bars colored by the type of distance (spec-z, photo-z, z-ind). But, there will probably be other plots that you can think of and make too to help us decide!

I think to do this I would suggest making a new branch on the TROVE repo for each, replacing this function in TROVE with the scoring method you are testing, then running it on all candidates for a GW event and saving the individual distance scores