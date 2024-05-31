def bin_spectra(waves, fluxes, b):
    """
    Bins spectra given list of wavelengths, fluxes, and binning factor
    """
    binned_waves = []
    binned_flux = []
    newindex = 0
    for index in range(0, len(fluxes), b):
        if index + b - 1 <= len(fluxes) - 1:
            sumx = 0
            sumy = 0
            for binindex in range(index, index+b, 1):
                if binindex < len(fluxes):
                    sumx += waves[binindex]
                    sumy += fluxes[binindex]

            sumx = sumx / b
            sumy = sumy / b
        if sumx > 0:
            binned_waves.append(sumx)
            binned_flux.append(sumy)

    return binned_waves, binned_flux
