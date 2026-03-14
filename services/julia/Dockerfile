FROM julia:1.10

WORKDIR /app

# Install packages in the image
RUN julia -e "using Pkg; Pkg.add([\"HTTP\",\"JSON3\",\"JuMP\",\"HiGHS\"]); Pkg.precompile(); using HTTP, JSON3, JuMP, HiGHS"

COPY optimizer.jl /app/optimizer.jl

EXPOSE 8081
CMD ["julia", "/app/optimizer.jl"]
